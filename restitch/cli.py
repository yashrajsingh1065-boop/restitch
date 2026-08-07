"""restitch CLI — run / introspect / map / verify / demo.

    restitch demo                         synthetic end-to-end run (start here)
    restitch introspect FILE              what a file looks like inside
    restitch map FILE --role soh          start a mapping profile from a file
    restitch run --manifest m.yaml --policy p.yaml
    restitch verify RUNDIR                re-run a run's manifest, compare plans

A run directory is a self-contained provenance bundle: plan.json (canonical
moves + metrics), provenance.json (input hashes + resolved policy + notes),
sanity.txt. Reproducing a run = re-running its manifest; `verify` does exactly
that and compares the canonical move lists.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

from . import __version__


def _load_policy(path):
    from .core.policy import from_dict
    return from_dict(yaml.safe_load(Path(path).read_text()) or {})


def _run_pipeline(man_path, policy_path, out: Path, *, preset=None,
                  allow_missing=(), skip_sanity=False, allow_red=False) -> int:
    from .core import engine
    from .core.summary import plan_json, text_summary
    from .io.manifest import load_manifest, raw_row_counts, resolve
    from .io.provenance import build_provenance
    from .io.sanity import render, run_sanity, worst

    man = load_manifest(man_path)
    pol = _load_policy(policy_path)
    if preset == "permissive":
        from .core.policy import permissive
        pol = permissive(pol)
    rr = resolve(man, pol, allow_missing=allow_missing)
    for n in rr.notes:
        print(f"  {n}")

    out.mkdir(parents=True, exist_ok=True)
    sanity_txt = "sanity: skipped (--skip-sanity)"
    if not skip_sanity:
        findings = run_sanity(rr.inputs, rr.policy, raw_row_counts(man))
        sanity_txt = render(findings)
        print()
        print(sanity_txt)
        if worst(findings) == "red" and not allow_red:
            (out / "sanity.txt").write_text(sanity_txt + "\n")
            print("\nRED sanity findings block the run — fix the inputs or "
                  "re-run with --allow-red to overrule them explicitly.")
            return 2
    (out / "sanity.txt").write_text(sanity_txt + "\n")

    print("\nrunning engine ...")
    R = engine.run(rr.inputs, rr.policy)

    plan = plan_json(R)
    if rr.policy.geo_mode != "pan":
        # price the scope by deterministic re-run with geography open — the only
        # honest number; GEO-BLOCKED counts overstate what opening would unlock
        from .core.summary import scope_cost
        print("\npricing the geo scope (re-running with geography open) ...")
        cost = scope_cost(R, engine.run(rr.inputs, rr.policy.with_(geo_mode="pan")))
        plan["scope_cost"] = cost
        print(f"geo scope '{cost['mode']}' costs {cost['units_cost']:,.0f} units · "
              f"{cost['sets_cost']} sets vs PAN "
              f"(cut {cost['cut_full']:.1%} scoped vs {cost['cut_full_pan']:.1%} open · "
              f"{cost['geo_blocked_sets']} heals geo-blocked at close)")
    plan["manifest"] = str(Path(man_path).resolve())
    (out / "plan.json").write_text(json.dumps(plan, indent=1))
    prov = build_provenance(rr.files, rr.policy, soh_asof=man.soh_asof)
    prov["notes"] = rr.notes
    prov["manifest"] = str(Path(man_path).resolve())
    (out / "provenance.json").write_text(json.dumps(prov, indent=1))

    print()
    print(text_summary(R))

    from .core.verify import verify_workbook
    from .render.workbook import render_workbook
    xlsx = out / "movement.xlsx"
    print(f"\nrendering workbook -> {xlsx.name} ...")
    battery = render_workbook(R, xlsx)
    print(f"build battery: {battery.summary()}")
    print("independent verify (replays the SAVED workbook) ...")
    vb = verify_workbook(xlsx, rr.inputs, rr.policy)   # default labels both sides
    print(f"verify: {vb.summary()}")

    print(f"\nrun dir: {out}  (movement.xlsx · plan.json · provenance.json · sanity.txt)")
    return 0


def cmd_run(a) -> int:
    out = Path(a.out) if a.out else Path("runs") / \
        datetime.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    return _run_pipeline(a.manifest, a.policy, out, preset=a.preset,
                         allow_missing=tuple(a.allow_missing or ()),
                         skip_sanity=a.skip_sanity, allow_red=a.allow_red)


def cmd_demo(a) -> int:
    from .synth import write_demo
    out = Path(a.out or "restitch-demo")
    man = write_demo(out, seed=a.seed, vocab=a.vocab)
    print(f"demo bundle written to {out}/ (raw files + mapping profiles + "
          "policy.yaml + manifest.yaml)\n")
    return _run_pipeline(man, out / "policy.yaml", out / "run")


def cmd_introspect(a) -> int:
    from .io.mapping import introspect
    from .io.readers import read_sheet, sheet_names
    names = sheet_names(a.file)
    sheet = a.sheet or names[0]
    print(f"{a.file} — sheets: {', '.join(names)} (showing {sheet!r})")
    info = introspect(read_sheet(a.file, sheet))
    print(f"suggested header row: {info['header_row']}   "
          "(0-based; verify before trusting)")
    for h, samples in info["headers"].items():
        print(f"  {h:32s} {' | '.join(samples)}")
    return 0


def cmd_map(a) -> int:
    import difflib

    from .io.mapping import CANONICAL_FIELDS, introspect
    from .io.readers import read_sheet, sheet_names
    fields = CANONICAL_FIELDS.get(a.role)
    if not fields:
        print(f"unknown role {a.role!r}; roles: {', '.join(CANONICAL_FIELDS)}")
        return 2
    sheet = a.sheet or sheet_names(a.file)[0]
    info = introspect(read_sheet(a.file, sheet))
    low = {h.lower(): h for h in info["headers"]}
    lines = [f"role: {a.role}",
             f"name: {a.name or Path(a.file).stem}",
             f"sheet: {sheet}",
             f"header_row: {info['header_row']}",
             "columns:"]
    for f2 in fields:
        near = difflib.get_close_matches(f2.lower(), list(low), n=1, cutoff=0.4)
        hint = "  # suggestion — CONFIRM against the sheet" if near else \
               "  # no candidate found; map it or delete this line"
        lines.append(f"  {f2}: {low[near[0]] if near else '???'}{hint}")
    lines += ["semantics: {}   # see restitch.io.mapping docs for this role's keys"]
    text = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).write_text(text)
        print(f"skeleton profile written to {a.out} — every suggested header "
              "needs a human eye before first use")
    else:
        print(text)
    return 0


def cmd_serve(a) -> int:
    try:
        import uvicorn

        from .web import create_app
    except ImportError:
        print("the web console needs the [web] extra:\n"
              "    pip install restitch[web]")
        return 2
    runs = Path(a.runs).resolve()
    runs.mkdir(parents=True, exist_ok=True)
    print(f"restitch console → http://{a.host}:{a.port}   (runs live in {runs})")
    uvicorn.run(create_app(runs), host=a.host, port=a.port, log_level="warning")
    return 0


def cmd_verify(a) -> int:
    from .core import engine
    from .core.policy import from_dict
    from .core.summary import canonical_moves
    from .io.manifest import load_manifest, resolve
    from .io.provenance import file_sha256

    rundir = Path(a.rundir)
    plan = json.loads((rundir / "plan.json").read_text())
    prov = json.loads((rundir / "provenance.json").read_text())

    drift = []
    for role, files in prov["files"].items():
        for f2 in files:
            p = Path(f2["path"])
            if not p.exists():
                drift.append(f"{role}: {p} is gone")
            elif file_sha256(p) != f2["sha256"]:
                drift.append(f"{role}: {p} changed since the run")
    if drift:
        print("INPUTS CHANGED — this is no longer the same run:")
        for d in drift:
            print(f"  {d}")
        return 2

    man = load_manifest(prov["manifest"])
    pol = from_dict({k: v for k, v in prov["policy"].items()})
    rr = resolve(man, pol)
    R = engine.run(rr.inputs, rr.policy)
    got = [list(t) for t in canonical_moves(R)]
    want = plan["moves"]
    if got == want:
        print(f"VERIFY GREEN: {len(got)} canonical move lines reproduced exactly "
              "from the same inputs and policy")
        return 0
    first = next((i for i, (g, w) in enumerate(zip(got, want, strict=False)) if g != w),
                 min(len(got), len(want)))
    print(f"VERIFY DRIFT: {len(got)} vs {len(want)} lines; first divergence at "
          f"index {first}:\n  got  {got[first] if first < len(got) else '—'}"
          f"\n  want {want[first] if first < len(want) else '—'}")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="restitch",
        description="set-completion inventory redeployment with a proof chain")
    ap.add_argument("--version", action="version",
                    version=f"restitch {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a manifest through sanity + engine")
    r.add_argument("--manifest", required=True)
    r.add_argument("--policy", required=True)
    r.add_argument("--preset", choices=("permissive",),
                   help="permissive = every guard off; the costing ceiling")
    r.add_argument("--out", help="run directory (default runs/run-<stamp>)")
    r.add_argument("--allow-missing", action="append", metavar="ROLE",
                   help="accept a missing required role as deliberate degradation")
    r.add_argument("--skip-sanity", action="store_true")
    r.add_argument("--allow-red", action="store_true",
                   help="run despite RED sanity findings (explicit overrule)")
    r.set_defaults(fn=cmd_run)

    d = sub.add_parser("demo", help="write the synthetic bundle and run it")
    d.add_argument("--out", help="target dir (default ./restitch-demo)")
    d.add_argument("--seed", type=int, default=8)
    d.add_argument("--vocab", choices=("tailoring", "footwear-uk"),
                   default="tailoring",
                   help="category shape of the synthetic network")
    d.set_defaults(fn=cmd_demo)

    i = sub.add_parser("introspect", help="peek inside an export file")
    i.add_argument("file")
    i.add_argument("--sheet")
    i.set_defaults(fn=cmd_introspect)

    m = sub.add_parser("map", help="draft a mapping profile from a file")
    m.add_argument("file")
    m.add_argument("--role", required=True)
    m.add_argument("--sheet")
    m.add_argument("--name")
    m.add_argument("-o", "--out")
    m.set_defaults(fn=cmd_map)

    v = sub.add_parser("verify", help="re-run a run dir's manifest, compare plans")
    v.add_argument("rundir")
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("serve", help="local web console (FastAPI, no database)")
    s.add_argument("--runs", default="restitch-runs",
                   help="runs root directory (default ./restitch-runs)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8642)
    s.set_defaults(fn=cmd_serve)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
