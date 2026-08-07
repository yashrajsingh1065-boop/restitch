"""Mapping profiles — how a tenant's export files map onto canonical fields.

A profile is written ONCE per file shape (usually once per category/tenant),
persisted as YAML, and reused every run. The engine never sees a raw header:
loaders resolve columns through the profile and FAIL LOUDLY on any miss with
the nearest candidates named — a renamed or reordered export must never load
silently wrong.

Three kinds of knowledge live in a profile, all tenant data vocabulary:
  · geometry    — sheet name, header row index
  · columns     — canonical field -> exact header text
  · semantics   — value maps, filters, derived-field rules, money-column
                  extendedness, channel-exclusion strings

The presumed-extended-money law: a stock file's money column is EXTENDED
(unit × row qty) until proven otherwise. The profile must DECLARE it either
way; the sanity layer cross-checks the declaration against the data.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class MappingError(RuntimeError):
    pass


# canonical field registries per role — what a loader may ask a profile for
CANONICAL_FIELDS = {
    "soh": ["division", "location", "category", "size", "style", "qty",
            "mrp", "value", "season", "dept", "ean"],
    "master": ["id", "name", "city", "state", "tier", "lat", "lon", "region",
               "grade", "model", "opened", "l2l", "status", "store_type", "format"],
    "norms": ["location", "brand_scope", "category", "qty"],
    "ros": ["location", "style", "ros8", "ros4", "sales8", "sales4", "grn8",
            "ros_life", "str_life", "sales_life", "dept", "shelf_life",
            "nsv_life", "nsv8", "inwards"],
    "fs_totals": ["style", "last_inward", "dept", "category", "season", "mrp"],
    "fs_split": ["style", "plant", "size", "qty", "brand", "category"],
    "tiers": ["location", "tier"],
}


def resolve(header_cells, wanted: str, where: str) -> int:
    """Exact (case/space-insensitive) header match or a loud failure naming the
    nearest candidates. Never guesses."""
    hdr = [str(h).strip() if h is not None else "" for h in header_cells]
    low = [h.lower() for h in hdr]
    w = wanted.strip().lower()
    if w in low:
        return low.index(w)
    near = difflib.get_close_matches(wanted.strip(), [h for h in hdr if h], n=3, cutoff=0.5)
    raise MappingError(
        f"{where}: mapped column {wanted!r} not found"
        + (f"; nearest: {', '.join(repr(n) for n in near)}" if near else
           f"; sheet headers: {[h for h in hdr if h][:12]}"))


@dataclass
class RoleMapping:
    """One file-shape → canonical-field mapping for one role."""
    role: str
    name: str = ""                      # profile id, e.g. "demo-soh"
    sheet: str | None = None
    header_row: int = 0                 # 0-based index of the header row
    data_row: int | None = None         # defaults to header_row + 1
    columns: dict = field(default_factory=dict)     # canonical field -> header text
    semantics: dict = field(default_factory=dict)   # role-specific knobs (below)
    # Documented semantics keys by role:
    #  (any)   id_pattern: regex — canonical ids failing it read as NO id
    #          (tenant junk-row cleaning, e.g. '^\\d+$' drops prose rows)
    #  soh:    sheets: [{name, divisions_in: [..]}] (overrides sheet), mrp_extended: bool,
    #          category_values: {canonical: [raw,..]}
    #  master: id_alias: {old: new}, blocked_when_nonempty: [col..],
    #          blocked_when_prefix: {col: prefix}, channel_exclude_name_contains: str,
    #          required_columns: [canonical..], status_running_value: str
    #  norms:  brand_scope_value: str, category_values: {canonical: [raw..]}
    #  ros:    kind: base|refresh|authoritative, clamp_negative_ros: bool,
    #          evidence_prefix: str (fields stored as <prefix>sold8 etc.)
    #  fs:     totals: {...}, split: {...} — see loaders.load_fs
    #  tiers:  (none)

    def __post_init__(self):
        known = CANONICAL_FIELDS.get(self.role.split(":")[0].split("-")[0])
        if known:
            bad = [k for k in self.columns if k not in known]
            if bad:
                raise MappingError(
                    f"profile {self.name or self.role}: unknown canonical fields {bad}; "
                    f"role '{self.role}' knows {known}")

    @property
    def start(self) -> int:
        return self.data_row if self.data_row is not None else self.header_row + 1

    def index(self, rows, *fields, optional=()) -> dict:
        """Resolve canonical fields to column indexes against the sheet's real
        header row. Missing optional fields resolve to None."""
        hdr = rows[self.header_row]
        out = {}
        for f2 in fields:
            if f2 not in self.columns:
                if f2 in optional:
                    out[f2] = None
                    continue
                raise MappingError(
                    f"profile {self.name or self.role}: canonical field {f2!r} is not mapped")
            out[f2] = resolve(hdr, self.columns[f2], f"{self.name or self.role}")
        for f2 in optional:
            if f2 not in out:
                out[f2] = (resolve(hdr, self.columns[f2], self.name)
                           if f2 in self.columns else None)
        return out


def load_profile(path) -> RoleMapping:
    d = yaml.safe_load(Path(path).read_text())
    known = {"role", "name", "sheet", "header_row", "data_row", "columns", "semantics"}
    extra = set(d) - known
    if extra:
        raise MappingError(f"{path}: unknown profile keys {sorted(extra)}")
    return RoleMapping(**d)


def introspect(rows, max_rows: int = 15) -> dict:
    """Header-row suggestion + per-candidate header list with sample values —
    the raw material for the mapping UI/CLI. Suggests, never auto-accepts."""
    best, best_score = 0, -1
    for i, r in enumerate(rows[:max_rows]):
        vals = [str(c).strip() for c in r if c is not None and str(c).strip()]
        score = len(set(vals))
        if score > best_score:
            best, best_score = i, score
    hdr = rows[best] if rows else []
    samples = {}
    for j, h in enumerate(hdr):
        name = str(h).strip() if h is not None else ""
        if not name:
            continue
        col = [r[j] for r in rows[best + 1:best + 6] if len(r) > j and r[j] is not None]
        samples[name] = [str(c)[:40] for c in col[:3]]
    return dict(header_row=best, headers=samples)
