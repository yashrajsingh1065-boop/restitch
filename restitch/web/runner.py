"""Run store + background execution. The filesystem is the database.

Layout of one run (self-contained provenance bundle):

    runs/<id>/
      inputs/            uploaded raw files, original names sanitized
      mappings/          one profile YAML per role
      manifest.yaml      built from the files step — the SAME manifest the CLI
                         takes; `restitch run --manifest ...` reproduces the run
      policy.yaml        the uploaded base policy (the preset)
      overrides.yaml     lever overrides from the configure step (may be empty)
      status.json        state machine + stage + headline snapshot
      log.txt            child stdout/traceback on failure
      sanity.json/.txt   findings, recorded at review AND re-run at execute
      out/               plan.json · provenance.json · checks.json · movement.xlsx

Execution runs in a `multiprocessing.Process`: the engine is CPU-bound and its
invariants ASSERT — a crashing run must land as a `failed` status with the
assertion text visible, never as a dead server.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import multiprocessing
import re
import traceback
from pathlib import Path

import yaml

STATES = ("files", "sanity", "configure", "review", "running", "done", "failed")
STAGES = ("load inputs", "input sanity", "engine — phases A–E",
          "render workbook", "independent verify")

_ID_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def run_dir(root: Path, rid: str) -> Path:
    if not _ID_RX.fullmatch(rid):
        raise ValueError(f"bad run id {rid!r}")
    d = (root / rid).resolve()
    if d.parent != root.resolve():
        raise ValueError(f"bad run id {rid!r}")
    return d


def new_run(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    rid, n = stamp, 1
    while (root / rid).exists():
        n += 1
        rid = f"{stamp}-{n}"
    d = root / rid
    (d / "inputs").mkdir(parents=True)
    (d / "mappings").mkdir()
    (d / "out").mkdir()
    write_status(d, state="files", created=_now())
    return rid


def clone_run(root: Path, src: Path, *, with_inputs: bool) -> str:
    """New run seeded from an existing one — the habit loop.

    Rerun (with_inputs=False): mappings, policy and lever overrides travel;
    the files step asks only for fresh raw exports. Duplicate (True): the raw
    inputs, manifest and sanity record travel too, so the clone opens at the
    levers step and a compare against its source prices exactly one change.
    """
    import shutil
    rid = new_run(root)
    d = root / rid
    for p in sorted((src / "mappings").glob("*.yaml")):
        shutil.copy2(p, d / "mappings" / p.name)
    for name in ("policy.yaml", "overrides.yaml"):
        if (src / name).exists():
            shutil.copy2(src / name, d / name)
    st = read_status(src)
    carry = dict(origin=dict(kind="duplicate" if with_inputs else "rerun",
                             src=src.name),
                 preset=st.get("preset"))
    if with_inputs:
        for p in sorted((src / "inputs").iterdir()):
            shutil.copy2(p, d / "inputs" / p.name)
        for name in ("manifest.yaml", "sanity.json", "sanity.txt"):
            if (src / name).exists():
                shutil.copy2(src / name, d / name)
        # the sanity decision was made against these exact inputs — it travels
        # with them, red overrule included (the hash pins it to the findings)
        carry.update(state="configure",
                     allow_missing=st.get("allow_missing", []),
                     notes=st.get("notes", []),
                     allow_red=st.get("allow_red", False),
                     red_hash=st.get("red_hash"),
                     sanity_acknowledged=st.get("sanity_acknowledged", False))
    write_status(d, **carry)
    return rid


def read_status(d: Path) -> dict:
    p = d / "status.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        # a torn read can only be a concurrent replace — settle and retry once
        import time
        time.sleep(0.05)
        return json.loads(p.read_text())


def write_status(d: Path, **kw) -> dict:
    """Atomic replace — the UI polls this file while the child writes it; a
    torn write must never be observable (review M5c)."""
    import os
    st = read_status(d)
    st.update(kw, updated=_now())
    tmp = d / "status.json.tmp"
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, d / "status.json")
    return st


def liveness(d: Path) -> dict:
    """Status with a dead-worker guard: a child that died BEFORE execute_run
    could report (spawn failure, kill -9) must land as failed, never as a
    forever-'running' run. Catches OSError BROADLY — a foreign or reused pid
    once raised PermissionError here and 500'd every runs page permanently
    (review M5a); any probe failure now reads as a dead worker."""
    import os
    st = read_status(d)
    if st.get("state") == "running" and st.get("pid"):
        try:
            os.kill(int(st["pid"]), 0)
        except ProcessLookupError:
            st = write_status(d, state="failed",
                              error="worker process died before reporting — "
                                    "see log.txt if present")
        except OSError:
            st = write_status(d, state="failed",
                              error="worker pid no longer belongs to this run "
                                    "(reused or foreign) — treating as dead")
    return st


def list_runs(root: Path) -> list[dict]:
    """Newest first; each with status + headline metrics when the run finished."""
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), reverse=True):
        if not (d / "status.json").exists():
            continue
        st = liveness(d)
        row = dict(id=d.name, **st)
        plan = d / "out" / "plan.json"
        if plan.exists():
            with contextlib.suppress(json.JSONDecodeError, KeyError):
                row["metrics"] = json.loads(plan.read_text())["metrics"]
        out.append(row)
    return out


def discard_stub(d: Path) -> None:
    """Remove a run that never executed. Anything that produced a plan is a
    provenance record — the UI never deletes those."""
    import shutil
    st = read_status(d)
    if st.get("state") not in ("files", "sanity", "configure", "review"):
        raise ValueError("only a never-executed run can be discarded")
    if (d / "out" / "plan.json").exists():
        raise ValueError("this run produced a plan — a record, not a stub")
    shutil.rmtree(d)


def input_fingerprints(d: Path) -> dict | None:
    """role -> sorted sha256 list, from the run's own provenance record.
    None when the run never produced one (it never executed)."""
    p = d / "out" / "provenance.json"
    if not p.exists():
        return None
    prov = json.loads(p.read_text())
    return {role: sorted(f["sha256"] for f in fs)
            for role, fs in prov.get("files", {}).items()}


def base_policy_dict(d: Path) -> dict:
    return yaml.safe_load((d / "policy.yaml").read_text()) or {}


def overrides_dict(d: Path) -> dict:
    p = d / "overrides.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def resolved_policy(d: Path):
    from ..core.policy import from_dict
    return from_dict({**base_policy_dict(d), **overrides_dict(d)})


def load_resolved(d: Path):
    """manifest + policy + overrides -> ResolvedRun (the CLI's own path)."""
    from ..io.manifest import load_manifest, resolve
    st = read_status(d)
    man = load_manifest(d / "manifest.yaml")
    pol = resolved_policy(d)
    return man, resolve(man, pol, allow_missing=tuple(st.get("allow_missing", ())))


# ───────────────────────── the child process ─────────────────────────
def execute_run(run_dir_str: str) -> None:
    """Child entry point — importable at module level for spawn start method.
    Every stage lands in status.json; any exception lands in log.txt and a
    `failed` status carrying the message. The pipeline is the CLI pipeline."""
    import os
    d = Path(run_dir_str)

    def stage(name):
        # the CHILD owns pid: os.getpid() here, not a racy parent write
        write_status(d, state="running", stage=name, pid=os.getpid())

    try:
        stage(STAGES[0])
        man, rr = load_resolved(d)

        stage(STAGES[1])
        from ..io.manifest import raw_row_counts
        from ..io.sanity import render as sanity_render
        from ..io.sanity import run_sanity, worst
        findings = run_sanity(rr.inputs, rr.policy, raw_row_counts(man))
        (d / "sanity.txt").write_text(sanity_render(findings) + "\n")
        (d / "sanity.json").write_text(json.dumps(
            [vars(f) for f in findings], indent=1))
        st = read_status(d)
        if worst(findings) == "red" and (
                not st.get("allow_red")
                or st.get("red_hash") != red_hash(findings)):
            # an overrule authorizes the findings it was TYPED against, not
            # whatever reds exist now (review M5e: the flag was sticky across
            # input changes)
            raise AssertionError(
                "RED sanity findings block the run; the recorded overrule "
                "does not cover the current findings — review them on the "
                "sanity page")

        stage(STAGES[2])
        from ..core import engine
        from ..core.summary import plan_json, scope_cost
        R = engine.run(rr.inputs, rr.policy)
        plan = plan_json(R)
        if rr.policy.geo_mode != "pan":
            R_pan = engine.run(rr.inputs, rr.policy.with_(geo_mode="pan"))
            plan["scope_cost"] = scope_cost(R, R_pan)
        plan["manifest"] = str((d / "manifest.yaml").resolve())
        (d / "out" / "plan.json").write_text(json.dumps(plan, indent=1))

        from ..io.provenance import build_provenance
        prov = build_provenance(rr.files, rr.policy, soh_asof=man.soh_asof)
        prov["notes"] = rr.notes
        prov["manifest"] = plan["manifest"]
        (d / "out" / "provenance.json").write_text(json.dumps(prov, indent=1))

        stage(STAGES[3])
        from ..render.workbook import render_workbook
        labels = dict(soh_asof=str(man.soh_asof or ""))
        battery = render_workbook(R, d / "out" / "movement.xlsx", labels=labels)

        stage(STAGES[4])
        from ..core.verify import verify_workbook
        vb = verify_workbook(d / "out" / "movement.xlsx", rr.inputs,
                             rr.policy, labels=labels)

        def ser(b):
            return [dict(id=c.id, section=c.section, name=c.name, passed=c.passed,
                         skipped=c.skipped, warn=c.warn, detail=c.detail)
                    for c in b.checks]

        (d / "out" / "checks.json").write_text(json.dumps(
            dict(battery=ser(battery), battery_summary=battery.summary(),
                 verify=ser(vb), verify_summary=vb.summary()), indent=1))

        m = plan["metrics"]
        write_status(d, state="done", stage=None,
                     headline=dict(lines=m["lines"], units=m["units"],
                                   cut=m["cut_pct"], categories=m["categories"],
                                   checks=battery.summary(), verify=vb.summary()))
    except BaseException as e:  # noqa: BLE001 — assertion text must reach the buyer
        (d / "log.txt").write_text(traceback.format_exc())
        write_status(d, state="failed", error=f"{type(e).__name__}: {e}")


def red_hash(findings) -> str:
    """Identity of a set of RED findings — what a typed overrule authorizes."""
    import hashlib
    reds = sorted((f["id"] if isinstance(f, dict) else f.id,
                   f["title"] if isinstance(f, dict) else f.title)
                  for f in findings
                  if (f["level"] if isinstance(f, dict) else f.level) == "red")
    return hashlib.sha256(json.dumps(reds).encode()).hexdigest()[:16]


def start(d: Path) -> None:
    write_status(d, state="running", stage="starting", error=None)
    p = multiprocessing.get_context("spawn").Process(
        target=execute_run, args=(str(d),), daemon=False)
    p.start()
    write_status(d, pid=p.pid)
