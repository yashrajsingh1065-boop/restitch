"""Run manifest — the one file that names a run's inputs.

A manifest binds each input ROLE to a file + mapping profile, plus the stock
as-of date. It is the reproducibility unit: `restitch run --manifest m.yaml`
must always mean the same run, so every path, profile and date lives here and
nothing is inferred from filenames.

Roles and the degradation matrix (what each absence costs) are stated in code
below and printed at load — a silently absent input is how wrong plans ship.

The as-of date drives the derived dates: a policy that pins new_door_cutoff or
season_current_index keeps its pinned value (tenant presets do this so a plan
is reproducible after the fact); an unpinned policy derives both from soh_asof.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..core.model import EngineInputs, Holding
from ..core.policy import Policy
from .loaders import load_fs, load_holdings, load_norms, load_ros, load_stores, load_tiers
from .mapping import MappingError, load_profile
from .readers import read_sheet

REQUIRED_ROLES = ("master", "soh", "norms")
OPTIONAL_ROLES = ("ros", "norms_context", "fs", "tiers")

#: role -> what the plan loses without it. Printed, returned, never silent.
DEGRADATION = {
    "ros": ("no velocity overlays: every holding runs as NO-DATA proxy — RoS "
            "gates judge nothing, chosen programs rank on nothing"),
    "norms": ("no norms: fill floors, peak caps and the no-norm rule have no "
              "basis — refused unless explicitly allowed, and evac_no_norm is "
              "forced off (every door would otherwise read as no-norm and "
              "evacuate)"),
    "norms_context": "no context norms: the report's context-norm column stays empty",
    "fs": "no free-stock file: the warehouse FS gap report will be empty",
    "tiers": "no tier file: report bands collapse to a single '—' band",
}


@dataclass
class Manifest:
    soh_asof: datetime.date | None
    roles: dict                      # role -> spec dict (ros: list of specs)
    base: Path                       # directory relative paths resolve against
    path: Path | None = None         # where this manifest was loaded from


@dataclass
class ResolvedRun:
    """Everything a run needs, loaded and reconciled."""
    inputs: EngineInputs
    policy: Policy                   # manifest-derived dates + degradation forced-off levers
    notes: list = field(default_factory=list)     # degradation + derivation notes
    files: dict = field(default_factory=dict)     # role -> [Path] (for provenance)


def load_manifest(path) -> Manifest:
    p = Path(path)
    d = yaml.safe_load(p.read_text())
    known_top = {"soh_asof", "base_dir", "roles"}
    extra = sorted(set(d) - known_top)
    if extra:
        raise MappingError(f"{p}: unknown manifest keys {extra}; known: {sorted(known_top)}")
    roles = d.get("roles") or {}
    bad = sorted(set(roles) - set(REQUIRED_ROLES) - set(OPTIONAL_ROLES))
    if bad:
        raise MappingError(
            f"{p}: unknown roles {bad}; required {list(REQUIRED_ROLES)}, "
            f"optional {list(OPTIONAL_ROLES)}")
    for role, spec in roles.items():
        specs = spec if isinstance(spec, list) else [spec]
        for s in specs:
            if "path" not in s:
                raise MappingError(f"{p}: role {role!r} spec needs a 'path'")
            needs = ("totals_profile", "split_profile") if role == "fs" else ("profile",)
            for k in needs:
                if k not in s:
                    raise MappingError(f"{p}: role {role!r} spec needs {k!r}")
    asof = d.get("soh_asof")
    if isinstance(asof, str):
        asof = datetime.date.fromisoformat(asof)
    if isinstance(asof, datetime.datetime):
        asof = asof.date()
    bd = d.get("base_dir")
    if bd:
        base = Path(bd)
        if not base.is_absolute():
            base = (p.parent / base).resolve()
    else:
        base = p.parent.resolve()
    return Manifest(soh_asof=asof, roles=roles, base=base, path=p)


def derive_policy(pol: Policy, soh_asof: datetime.date | None) -> tuple[Policy, list]:
    """Fill unpinned date-derived fields from the stock as-of date. Pinned
    values (tenant presets) always win — reproducibility beats freshness."""
    notes, kw = [], {}
    if soh_asof is None:
        if pol.new_door_donor_freeze and pol.new_door_cutoff is None:
            notes.append("WARN: no soh_asof and no pinned new_door_cutoff — the "
                         "new-door donor freeze is inert this run")
        if pol.season_current_index == 0 and pol.protect_fresh_season:
            notes.append("WARN: no soh_asof and no pinned season_current_index — "
                         "fresh-season protection cannot identify the current season")
        return pol, notes
    if pol.new_door_donor_freeze and pol.new_door_cutoff is None:
        cutoff = datetime.datetime(soh_asof.year, soh_asof.month, soh_asof.day) \
            - datetime.timedelta(days=pol.new_door_window_days)
        kw["new_door_cutoff"] = cutoff
        notes.append(f"derived new_door_cutoff = {cutoff.date()} "
                     f"(soh_asof - {pol.new_door_window_days}d)")
    if pol.season_current_index == 0:
        half = 1 if soh_asof.month >= pol.season_autumn_start_month else 0
        idx = soh_asof.year * 2 + half
        kw["season_current_index"] = idx
        notes.append(f"derived season_current_index = {idx} "
                     f"({'autumn' if half else 'spring'} {soh_asof.year})")
    return (pol.with_(**kw) if kw else pol), notes


def _paths(man: Manifest, role) -> list[Path]:
    spec = man.roles.get(role)
    if spec is None:
        return []
    specs = spec if isinstance(spec, list) else [spec]
    return [man.base / s["path"] for s in specs]


def resolve(man: Manifest, pol: Policy, *, tier_overrides=None,
            allow_missing=(), holding_cls=Holding) -> ResolvedRun:
    """Manifest + policy -> ResolvedRun. Missing required roles fail loudly;
    missing optional roles degrade EXPLICITLY (matrix above, notes returned)."""
    notes: list = []
    missing_required = [r for r in REQUIRED_ROLES
                        if r not in man.roles and r not in allow_missing]
    if missing_required:
        raise MappingError(
            f"manifest missing required roles {missing_required} "
            f"(--allow-missing accepts only a deliberate degradation)")

    def prof(spec_key, role):
        pp = Path(man.roles[role][spec_key])
        return load_profile(pp if pp.is_absolute() else man.base / pp)

    stores = load_stores(_paths(man, "master")[0], prof("profile", "master"))

    soh_prof = prof("profile", "soh")
    sn = soh_prof.semantics.get("size_normalizer", "numeric_zfill3")
    holdings, excluded, dept_votes = load_holdings(
        _paths(man, "soh")[0], soh_prof, stores, holding_cls=holding_cls,
        size_normalizer=sn)

    if "ros" in man.roles:
        specs = man.roles["ros"]
        specs = specs if isinstance(specs, list) else [specs]
        srcs = []
        for s in specs:
            pp = Path(s["profile"])
            srcs.append((man.base / s["path"],
                         load_profile(pp if pp.is_absolute() else man.base / pp)))
        perf, style_net = load_ros(srcs)
    else:
        perf, style_net = {}, {}
        notes.append(f"WARN [ros]: {DEGRADATION['ros']}")

    if "norms" in man.roles:
        norms = load_norms(_paths(man, "norms")[0], prof("profile", "norms"))
    else:
        norms = {}
        notes.append(f"WARN [norms]: {DEGRADATION['norms']}")
        if pol.evac_no_norm:
            pol = pol.with_(evac_no_norm=False)

    if "norms_context" in man.roles:
        norms_context = load_norms(_paths(man, "norms_context")[0],
                                   prof("profile", "norms_context"))
    else:
        norms_context = {}
        notes.append(f"note [norms_context]: {DEGRADATION['norms_context']}")

    if "fs" in man.roles:
        fs, fs_meta = load_fs(_paths(man, "fs")[0],
                              prof("totals_profile", "fs"), prof("split_profile", "fs"),
                              all_sizes=pol.all_sizes, size_normalizer=sn,
                              fallback_plants=pol.fs_warehouses)
    else:
        fs, fs_meta = {}, {}
        notes.append(f"note [fs]: {DEGRADATION['fs']}")

    if "tiers" in man.roles:
        tiers = load_tiers(_paths(man, "tiers")[0], prof("profile", "tiers"),
                           overrides=tier_overrides)
    else:
        tiers = dict(tier_overrides or {})
        notes.append(f"note [tiers]: {DEGRADATION['tiers']}")

    pol, dnotes = derive_policy(pol, man.soh_asof)
    notes.extend(dnotes)

    inputs = EngineInputs(
        stores=stores, tiers=tiers, holdings=holdings, excluded=excluded,
        dept_votes=dept_votes, perf=perf, style_net=style_net,
        norms=norms, norms_context=norms_context, fs=fs, fs_meta=fs_meta)
    files = {r: _paths(man, r) for r in man.roles}
    return ResolvedRun(inputs=inputs, policy=pol, notes=notes, files=files)


def raw_row_counts(man: Manifest) -> dict:
    """(role, filename, sheet) -> raw row count — the export-truncation scan's
    raw material. A separate pass so `resolve` stays single-purpose; skip it
    with --skip-sanity when speed matters."""
    counts = {}

    def count(role, path, sheet):
        try:
            counts[(role, Path(path).name, sheet or "")] = len(read_sheet(path, sheet))
        except Exception as e:                        # sanity must not kill the run
            counts[(role, Path(path).name, sheet or "")] = f"unreadable: {e}"

    for role, spec in man.roles.items():
        specs = spec if isinstance(spec, list) else [spec]
        for s in specs:
            path = man.base / s["path"]
            keys = [k for k in ("profile", "totals_profile", "split_profile") if k in s]
            for k in keys:
                pp = Path(s[k])
                prof = load_profile(pp if pp.is_absolute() else man.base / pp)
                sheets = ([sp["name"] for sp in prof.semantics.get("sheets", [])]
                          if prof.semantics.get("sheets") else [prof.sheet])
                for sh in sheets:
                    count(role, path, sh)
    return counts
