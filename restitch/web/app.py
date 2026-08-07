"""Routes — a console over the CLI pipeline, never a second pipeline.

IA is run-series-centric: home is the runs list (rerun is the habit); the
guided flow (files → mapping-on-mismatch → sanity → levers → review → run) is
the exception path for a category's first run. Every step is a page; there
are no modals. The report leads with checks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from ..io.mapping import MappingError
from . import runner
from .levers import GEO_MODES_HELP, LEVER_GROUPS, collect_overrides

HERE = Path(__file__).resolve().parent

ROLE_SLOTS = (
    # (role, human title, required, list-valued, note)
    ("soh", "Stock on hand", True, False,
     "the stock file — this IS the network"),
    ("master", "Store master", True, False,
     "store attributes: eligibility, geography, model"),
    ("norms", "Operative norms", True, False,
     "operative norms — floors and caps bind on these"),
    ("ros", "Velocity", False, True,
     "velocity — without it every holding runs NO-DATA"),
    ("tiers", "Tiers", False, False, "report bands only"),
    ("norms_context", "Context norms", False, False,
     "context norms — rendered, never binding"),
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def inputs_verdict(fa: dict | None, fb: dict | None):
    """('identical'|'differ'|'unknown', differing roles, total files).
    Two runs are honestly comparable only when every input file matches by
    sha256 — otherwise the delta prices the inputs, not the levers."""
    if fa is None or fb is None:
        return "unknown", [], 0
    roles = sorted(set(fa) | set(fb))
    diff = [r for r in roles if fa.get(r) != fb.get(r)]
    n = sum(len(v) for v in fa.values())
    return ("identical" if not diff else "differ"), diff, n


def _fmt_pol(v) -> str:
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def _fname(upload: UploadFile) -> str:
    return _SAFE_NAME.sub("_", Path(upload.filename or "file").name) or "file"


def create_app(runs_root: Path) -> FastAPI:
    runs_root = Path(runs_root)
    app = FastAPI(title="restitch", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")

    def page(request, name, rid=None, **ctx):
        st = runner.read_status(_dir(rid)) if rid else {}
        return tpl.TemplateResponse(
            request, name, dict(ctx, rid=rid, status=st))

    def _dir(rid: str) -> Path:
        return runner.run_dir(runs_root, rid)

    def _goto(rid: str, step: str) -> RedirectResponse:
        return RedirectResponse(f"/runs/{rid}/{step}" if step else f"/runs/{rid}",
                                status_code=303)

    def _try_resolve(d: Path):
        """(ok, failing_role, error). Profile names are rewritten to their role
        at upload, so a MappingError's message prefix routes to the map page."""
        try:
            _man, rr = runner.load_resolved(d)
            return True, rr, None
        except MappingError as e:
            msg = str(e)
            role = next((r for r, *_ in ROLE_SLOTS if msg.startswith(f"{r}:")),
                        None)
            return False, role, msg
        except Exception as e:  # noqa: BLE001 — surfaced on the files page
            return False, None, f"{type(e).__name__}: {e}"

    # ── home ────────────────────────────────────────────────────────────
    @app.get("/")
    def index(request: Request):
        runs = runner.list_runs(runs_root)
        done = [r for r in runs if r.get("state") == "done"]
        return page(request, "index.html", runs=runs, done=done)

    @app.post("/runs")
    def create_run():
        rid = runner.new_run(runs_root)
        return _goto(rid, "inputs")

    @app.get("/policy-template.yaml")
    def policy_template(vocab: str = "tailoring"):
        """A starting preset, generated from the Policy dataclass itself so the
        handed-out file can never drift from what from_dict accepts."""
        from ..core.policy import to_dict
        from ..synth import VOCABS, demo_policy
        if vocab not in VOCABS:
            return PlainTextResponse(
                f"unknown vocab {vocab!r}; one of: {', '.join(sorted(VOCABS))}",
                status_code=404)
        head = (f"# restitch policy preset — {vocab} starting template.\n"
                "# Edit the size vocabulary for your category; budgets here are\n"
                "# sized to the demo network. Every field is documented in\n"
                "# restitch/core/policy.py; unknown keys are rejected by name.\n")
        body = head + yaml.safe_dump(to_dict(demo_policy(vocab)), sort_keys=False)
        return Response(body, media_type="text/yaml", headers={
            "Content-Disposition": f'attachment; filename="policy-{vocab}.yaml"'})

    @app.post("/runs/{rid}/discard")
    def discard(rid: str):
        d = _dir(rid)
        try:
            runner.discard_stub(d)
        except (ValueError, FileNotFoundError):
            return _goto(rid, "")     # executed runs are records — bounce back
        return RedirectResponse("/", status_code=303)

    @app.post("/runs/{rid}/rerun")
    def rerun(rid: str):
        d = _dir(rid)
        if not (d / "policy.yaml").exists() or not any((d / "mappings").glob("*.yaml")):
            return _goto(rid, "")     # nothing reusable — not a rerunnable run
        nid = runner.clone_run(runs_root, d, with_inputs=False)
        return _goto(nid, "inputs")

    @app.post("/runs/{rid}/duplicate")
    def duplicate(rid: str):
        d = _dir(rid)
        if not (d / "manifest.yaml").exists() or not (d / "sanity.json").exists():
            return _goto(rid, "")     # inputs never loaded — nothing to duplicate
        nid = runner.clone_run(runs_root, d, with_inputs=True)
        return _goto(nid, "configure")

    # ── step 1 · files ──────────────────────────────────────────────────
    def _files_ctx(d: Path) -> dict:
        """What already lives in the run dir — a rerun carries profiles and
        policy forward, so the files step asks only for fresh raw exports."""
        st = runner.read_status(d)
        return dict(
            slots=ROLE_SLOTS,
            have_profiles={p.stem for p in (d / "mappings").glob("*.yaml")},
            have_policy=(d / "policy.yaml").exists(),
            origin=st.get("origin"))

    @app.get("/runs/{rid}/inputs")
    def inputs_page(request: Request, rid: str, error: str = ""):
        d = _dir(rid)
        return page(request, "files.html", rid, error=error, **_files_ctx(d))

    @app.post("/runs/{rid}/inputs")
    async def inputs_post(request: Request, rid: str):
        d = _dir(rid)
        form = await request.form()
        man_roles: dict = {}
        try:
            for role, _title, required, many, _note in ROLE_SLOTS:
                up = form.get(role)
                has_file = isinstance(up, UploadFile) and (up.filename or "")
                if not has_file:
                    if required:
                        raise ValueError(f"{role}: file is required")
                    continue
                prof_up = form.get(f"{role}_profile")
                prof_here = d / "mappings" / f"{role}.yaml"
                has_prof = isinstance(prof_up, UploadFile) and prof_up.filename
                if not has_prof and not prof_here.exists():
                    raise ValueError(f"{role}: mapping profile is required")
                raw = d / "inputs" / _fname(up)
                raw.write_bytes(await up.read())
                if has_prof:
                    prof = yaml.safe_load((await prof_up.read()).decode()) or {}
                    want = "fs" if role == "fs" else role
                    ok_roles = (want, role,
                                "norms" if role == "norms_context" else None)
                    if prof.get("role") not in ok_roles:
                        raise ValueError(
                            f"{role}: profile declares role {prof.get('role')!r}")
                    prof["name"] = role      # error messages route by role
                    prof_here.write_text(yaml.safe_dump(prof, sort_keys=False))
                # else: the profile carried over from the source run is reused
                entry = dict(path=f"inputs/{raw.name}",
                             profile=f"mappings/{role}.yaml")
                man_roles[role] = [entry] if many else entry
            pol_up = form.get("policy")
            has_pol = isinstance(pol_up, UploadFile) and pol_up.filename
            if not has_pol and not (d / "policy.yaml").exists():
                raise ValueError("policy: the category preset YAML is required")
            if has_pol:
                (d / "policy.yaml").write_bytes(await pol_up.read())
                runner.write_status(d, preset=_fname(pol_up))
            soh_asof = str(form.get("soh_asof") or "").strip()
            man = dict(roles=man_roles)
            if soh_asof:
                man["soh_asof"] = soh_asof
            (d / "manifest.yaml").write_text(yaml.safe_dump(man, sort_keys=False))
            allow = ["ros"] if ("ros" not in man_roles) else []
            for role in ("tiers", "norms_context", "fs"):
                if role not in man_roles:
                    allow.append(role)
            runner.write_status(d, allow_missing=allow)
        except ValueError as e:
            return page(request, "files.html", rid, error=str(e),
                        **_files_ctx(d))

        ok, who, msg = _try_resolve(d)
        if not ok:
            if who:
                runner.write_status(d, map_error=msg)
                return _goto(rid, f"map/{who}")
            return page(request, "files.html", rid, error=msg, **_files_ctx(d))
        return _finish_load(d, rid, who)

    def _finish_load(d: Path, rid: str, rr):
        from ..io.manifest import load_manifest, raw_row_counts
        from ..io.sanity import run_sanity
        man = load_manifest(d / "manifest.yaml")
        findings = run_sanity(rr.inputs, rr.policy, raw_row_counts(man))
        (d / "sanity.json").write_text(json.dumps(
            [vars(f) for f in findings], indent=1))
        # fresh inputs void any earlier overrule — it authorized OLD findings
        runner.write_status(d, state="sanity", notes=rr.notes, map_error=None,
                            allow_red=False, red_hash=None,
                            sanity_acknowledged=False)
        return _goto(rid, "sanity")

    # ── step 2 · mapping (surfaces only on mismatch) ────────────────────
    def _canonical_fields(role: str) -> list[str]:
        from ..io.mapping import CANONICAL_FIELDS
        return CANONICAL_FIELDS.get({"norms_context": "norms"}.get(role, role), [])

    @app.get("/runs/{rid}/map/{role}")
    def map_page(request: Request, rid: str, role: str, sheet: str = ""):
        d = _dir(rid)
        from ..io.mapping import introspect
        from ..io.readers import read_sheet, sheet_names
        prof_path = d / "mappings" / f"{role}.yaml"
        prof = yaml.safe_load(prof_path.read_text()) or {}
        man = yaml.safe_load((d / "manifest.yaml").read_text())
        entry = man["roles"][role]
        entry = entry[0] if isinstance(entry, list) else entry
        raw = d / entry["path"]
        names = sheet_names(raw)
        cur = sheet or prof.get("sheet")
        cur = cur if cur in names else names[0]
        info = introspect(read_sheet(raw, cur))
        return page(request, "map.html", rid, role=role, sheets=names,
                    sheet=cur, headers=info["headers"],
                    suggested_header_row=info["header_row"],
                    header_row=prof.get("header_row", info["header_row"]),
                    fields=_canonical_fields(role),
                    columns=prof.get("columns") or {},
                    profile_yaml=prof_path.read_text(),
                    error=runner.read_status(d).get("map_error", ""))

    @app.post("/runs/{rid}/map/{role}")
    async def map_post(request: Request, rid: str, role: str):
        d = _dir(rid)
        form = await request.form()
        prof_path = d / "mappings" / f"{role}.yaml"
        if form.get("profile_yaml") is not None:
            # the advanced escape hatch — full YAML, semantics and all
            try:
                prof = yaml.safe_load(str(form.get("profile_yaml"))) or {}
                prof["name"] = role
            except yaml.YAMLError as e:
                runner.write_status(
                    d, map_error=f"{role}: profile is not valid YAML — {e}")
                return _goto(rid, f"map/{role}")
        else:
            # per-field dropdowns of the file's own headers; the profile is
            # rebuilt server-side, semantics untouched — nobody writes YAML
            prof = yaml.safe_load(prof_path.read_text()) or {}
            prof["name"] = role
            sheet = str(form.get("sheet") or "").strip()
            if sheet:
                prof["sheet"] = sheet
            try:
                prof["header_row"] = int(str(form.get("header_row") or "0"))
            except ValueError:
                runner.write_status(
                    d, map_error=f"{role}: header row must be a number")
                return _goto(rid, f"map/{role}")
            prof["columns"] = {
                f2: str(form.get(f"col_{f2}")).strip()
                for f2 in _canonical_fields(role)
                if str(form.get(f"col_{f2}") or "").strip()}
        prof_path.write_text(yaml.safe_dump(prof, sort_keys=False))
        ok, who, msg = _try_resolve(d)
        if not ok:
            runner.write_status(d, map_error=msg)
            return _goto(rid, f"map/{who or role}")
        return _finish_load(d, rid, who)

    # ── step 3 · sanity review ──────────────────────────────────────────
    @app.get("/runs/{rid}/sanity")
    def sanity_page(request: Request, rid: str, error: str = ""):
        d = _dir(rid)
        findings = json.loads((d / "sanity.json").read_text())
        by = {lv: [f for f in findings if f["level"] == lv]
              for lv in ("red", "amber", "info")}
        return page(request, "sanity.html", rid, by=by, error=error)

    @app.post("/runs/{rid}/sanity")
    async def sanity_post(request: Request, rid: str):
        d = _dir(rid)
        form = await request.form()
        findings = json.loads((d / "sanity.json").read_text())
        reds = [f for f in findings if f["level"] == "red"]
        ambers = [f for f in findings if f["level"] == "amber"]
        if reds and str(form.get("overrule") or "").strip() != "OVERRULE":
            return sanity_page(request, rid,
                               error="RED findings block the run. Type OVERRULE "
                                     "to proceed against them — the overrule is "
                                     "recorded in the run's provenance.")
        if ambers and not form.get("ack"):
            return sanity_page(request, rid,
                               error="Acknowledge the amber findings to proceed "
                                     "— they travel into the run record either way.")
        runner.write_status(d, state="configure", allow_red=bool(reds),
                            red_hash=(runner.red_hash(findings) if reds else None),
                            sanity_acknowledged=True)
        return _goto(rid, "configure")

    # ── step 4 · levers ─────────────────────────────────────────────────
    def _configure_view(request, rid, *, error="", errors=None, raw=None):
        """raw = the submitted form on a rejected post — every field re-renders
        with exactly what the user typed, never a silent reset to the preset."""
        d = _dir(rid)
        base = runner.base_policy_dict(d)
        over = runner.overrides_dict(d)
        rawmap = {}
        if raw is not None:
            for _g, levers in LEVER_GROUPS:
                for field, _k, _l, _n in levers:
                    v = raw.get(field)
                    if v is not None:
                        rawmap[field] = str(v)

        def _shown(field, kind):
            if field in rawmap:
                return rawmap[field]
            if field in over:
                v = over[field]
                if kind == "bool":
                    return "1" if v else "0"
                if kind == "csv":
                    return ", ".join(v)
                return str(v)
            return ""
        shown = {field: _shown(field, kind)
                 for _g, levers in LEVER_GROUPS
                 for field, kind, _l, _n in levers}
        return page(request, "configure.html", rid, groups=LEVER_GROUPS,
                    base=base, over=over, geo_help=GEO_MODES_HELP,
                    error=error, errors=errors or {}, shown=shown)

    @app.get("/runs/{rid}/configure")
    def configure_page(request: Request, rid: str, error: str = ""):
        return _configure_view(request, rid, error=error)

    @app.post("/runs/{rid}/configure")
    async def configure_post(request: Request, rid: str):
        d = _dir(rid)
        form = await request.form()
        base = runner.base_policy_dict(d)
        over, errors = collect_overrides(form, base)
        if errors:
            return _configure_view(
                request, rid, errors=errors, raw=form,
                error="Fix the named levers below — nothing persisted.")
        try:
            from ..core.policy import from_dict
            from_dict({**base, **over})      # loud validation before anything persists
        except (ValueError, TypeError) as e:
            return _configure_view(request, rid, error=str(e), raw=form)
        (d / "overrides.yaml").write_text(
            yaml.safe_dump(over, sort_keys=False) if over else "{}\n")
        runner.write_status(d, state="review")
        return _goto(rid, "review")

    # ── step 5 · review ─────────────────────────────────────────────────
    @app.get("/runs/{rid}/review")
    def review_page(request: Request, rid: str):
        d = _dir(rid)
        from ..io.provenance import file_sha256
        base = runner.base_policy_dict(d)
        over = runner.overrides_dict(d)
        diff = [(k, base.get(k), v) for k, v in sorted(over.items())]
        hashes = [(p.name, file_sha256(p)[:16])
                  for p in sorted((d / "inputs").iterdir())]
        st = runner.read_status(d)
        return page(request, "review.html", rid, diff=diff, hashes=hashes,
                    notes=st.get("notes", ()), allow_red=st.get("allow_red"))

    @app.post("/runs/{rid}/execute")
    def execute(rid: str):
        d = _dir(rid)
        state = runner.liveness(d).get("state")
        if state not in ("review", "failed", "done"):
            # a mid-flow or already-running run never executes (review M5d:
            # double-execute once spawned two workers on one run dir)
            return _goto(rid, "")
        runner.start(d)
        return _goto(rid, "")

    # ── run page: progress / failed / report ────────────────────────────
    @app.get("/runs/{rid}")
    def run_page(request: Request, rid: str):
        d = _dir(rid)
        st = runner.liveness(d)
        state = st.get("state")
        if state in ("files",):
            return _goto(rid, "inputs")
        if state in ("sanity", "configure", "review"):
            return _goto(rid, state)
        if state == "running":
            return page(request, "progress.html", rid, stages=runner.STAGES)
        if state == "failed":
            log = (d / "log.txt")
            return page(request, "failed.html", rid,
                        log=log.read_text() if log.exists() else "")
        # done — the report, checks first
        checks = json.loads((d / "out" / "checks.json").read_text())
        plan = json.loads((d / "out" / "plan.json").read_text())
        findings = json.loads((d / "sanity.json").read_text()) \
            if (d / "sanity.json").exists() else []
        prov_p = d / "out" / "provenance.json"
        prov = json.loads(prov_p.read_text()) if prov_p.exists() else {}
        base = runner.base_policy_dict(d)
        over = runner.overrides_dict(d)
        by_sec: dict = {}
        for c in checks["battery"]:
            by_sec.setdefault(c["section"], []).append(c)

        def counted(items):
            coll = [c for c in items if not c["skipped"]]
            return dict(items=items, total=len(coll),
                        passed=sum(1 for c in coll if c["passed"]),
                        skipped=len(items) - len(coll))

        sections = [dict(name=sec, **counted(items))
                    for sec, items in by_sec.items()]
        vsec = counted(checks["verify"])
        failures = [c for c in checks["battery"] + checks["verify"]
                    if not c["passed"] and not c["skipped"] and not c.get("warn")]
        fingerprints = [(role, Path(f["path"]).name, f["sha256"][:16],
                         f["bytes"])
                        for role, fs in prov.get("files", {}).items()
                        for f in fs]
        return page(request, "report.html", rid, checks=checks,
                    sections=sections, vsec=vsec, failures=failures,
                    metrics=plan["metrics"], scope_cost=plan.get("scope_cost"),
                    ambers=[f for f in findings if f["level"] in ("red", "amber")],
                    diff=[(k, base.get(k), v) for k, v in sorted(over.items())],
                    manifest=str(d / "manifest.yaml"),
                    prov=prov, fingerprints=fingerprints,
                    prov_notes=prov.get("notes") or [])

    @app.get("/runs/{rid}/status")
    def status(rid: str):
        return JSONResponse(runner.liveness(_dir(rid)))

    @app.get("/runs/{rid}/download")
    def download(rid: str, file: str = "movement.xlsx"):
        if file not in ("movement.xlsx", "plan.json", "provenance.json",
                        "checks.json"):
            return JSONResponse({"error": "unknown file"}, status_code=404)
        p = _dir(rid) / "out" / file
        if not p.exists():
            return JSONResponse({"error": "not produced"}, status_code=404)
        return FileResponse(p, filename=f"{rid}-{file}")

    # ── compare ─────────────────────────────────────────────────────────
    @app.get("/compare")
    def compare(request: Request, a: str = "", b: str = ""):
        def load(rid):
            if not rid:
                return None
            p = _dir(rid) / "out" / "plan.json"
            return json.loads(p.read_text())["metrics"] if p.exists() else None

        ma, mb = load(a), load(b)
        rows = []
        verdict, diff_roles, n_files, lever_diff = None, [], 0, []
        if ma and mb:
            def row(label, va, vb, fmt="{:,.0f}", invert=False, unit=""):
                d_ = (vb - va) if isinstance(va, (int, float)) else None
                if d_ is None:
                    return
                good = (d_ or 0) * (-1 if invert else 1)
                rows.append(dict(
                    label=label, a=fmt.format(va), b=fmt.format(vb),
                    delta=(f"{d_:+,.1f}".rstrip("0").rstrip(".")
                           if isinstance(d_, float) else f"{d_:+,}"),
                    unit=unit, good=good,
                    arrow=("▲" if d_ > 0 else ("▼" if d_ < 0 else "")),
                    word=("better" if good > 0
                          else ("worse" if good < 0 else "no change"))))

            row("Units moved", ma["units"], mb["units"], unit="u")
            row("Lines", ma["lines"], mb["lines"], unit="lines")
            row("Cut % after full plan", ma["cut_pct"]["full"] * 100,
                mb["cut_pct"]["full"] * 100, "{:.2f}%", invert=True, unit="pp")
            row("Cut % after P1", ma["cut_pct"]["p1"] * 100,
                mb["cut_pct"]["p1"] * 100, "{:.2f}%", invert=True, unit="pp")
            row("Shipping km (after localization)",
                ma["localization"]["km_after"], mb["localization"]["km_after"],
                invert=True, unit="km")
            row("Doors planned", ma["stores_planned"], mb["stores_planned"],
                unit="doors")
            verdict, diff_roles, n_files = inputs_verdict(
                runner.input_fingerprints(_dir(a)),
                runner.input_fingerprints(_dir(b)))
            pa = {**runner.base_policy_dict(_dir(a)),
                  **runner.overrides_dict(_dir(a))}
            pb = {**runner.base_policy_dict(_dir(b)),
                  **runner.overrides_dict(_dir(b))}
            lever_diff = [(k, _fmt_pol(pa[k]) if k in pa else "—",
                           _fmt_pol(pb[k]) if k in pb else "—")
                          for k in sorted(set(pa) | set(pb))
                          if pa.get(k) != pb.get(k)]
        done = [r for r in runner.list_runs(runs_root) if r.get("state") == "done"]
        return page(request, "compare.html", a=a, b=b, ma=ma, mb=mb,
                    rows=rows, done=done, verdict=verdict,
                    diff_roles=diff_roles, n_files=n_files,
                    lever_diff=lever_diff)

    return app
