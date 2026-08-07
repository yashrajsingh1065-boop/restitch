"""Generic role loaders — every tenant file quirk is profile CONFIG, not code.

Each loader reproduces a battle-tested ingestion semantics:
  · stores    — master registry with derived flags (blocked / channel-exclude /
                junk), id re-keying onto the live stock code, 0-coord scrubbing
  · holdings  — stock file → Holding map with an EXCLUDED LEDGER (reason-tagged;
                nothing vanishes silently), extended-money division, category
                value maps, per-sheet division filters
  · norms     — scoped roll-up rows only, category value maps, qty > 0
  · ros       — ordered overlays: base → refresh → authoritative (stock-adjusted,
                negative-clamped, evidence fields kept SEPARATE from base sales
                so liveness tests keep one definition)
  · fs        — totals sheet × split-ratio sheet with 3-level fallback
  · tiers     — label map + tenant overrides (file wins)

The store master is a registry of ATTRIBUTES; the stock file IS the network.
Loaders never invent a door, and both ends of every exclusion are written to
the ledger with a reason — with ONE deliberate exception: non-positive qty
rows are ignored outright (owner decision, 2026-08-05).
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ..core.ids import store_id
from ..core.model import (
    Store,
    normalize_size_alpha,
    normalize_size_numeric3,
    normalize_size_uk_shoe,
)
from .mapping import MappingError, RoleMapping
from .readers import _dt, _f, read_sheet

NORMALIZERS = {
    "numeric_zfill3": normalize_size_numeric3,
    "uk_shoe": normalize_size_uk_shoe,
    "alpha": normalize_size_alpha,
    "verbatim": lambda x: str(x).strip(),
}


def _sem(prof: RoleMapping, key, default=None):
    return prof.semantics.get(key, default)


def _id_reader(prof: RoleMapping):
    """Store-id reader for one profile: the canonical law (core.ids) plus the
    profile's optional `id_pattern` semantics — a row whose canonical id fails
    the pattern reads as having NO id and takes the same skip path a blank id
    takes. This is the tenant's own junk-row cleaning as CONFIG (e.g. VH
    masters carry prose like 'Under Process' in the id column; its profiles
    declare id_pattern '^\\d+$'), never a hidden int() in code."""
    import re
    pat = _sem(prof, "id_pattern")
    rx = re.compile(pat) if pat else None

    def read(v):
        s = store_id(v)
        if s is None or (rx is not None and not rx.fullmatch(s)):
            return None
        return s
    return read


# ───────────────────────── stores (master) ─────────────────────────
def load_stores(path, prof: RoleMapping) -> dict:
    rows = read_sheet(path, prof.sheet)
    required = _sem(prof, "required_columns", [])
    for f2 in required:
        if f2 not in prof.columns:
            raise MappingError(f"{prof.name}: required canonical field {f2!r} unmapped")
    ix = prof.index(rows, "id",
                    optional=("name", "city", "state", "tier", "lat", "lon", "region",
                              "grade", "model", "opened", "l2l", "status",
                              "store_type", "format"))
    for f2 in required:
        if ix.get(f2) is None:
            raise MappingError(f"{prof.name}: required column for {f2!r} missing in sheet")
    sid = _id_reader(prof)
    alias = {store_id(k): store_id(v)
             for k, v in (_sem(prof, "id_alias", {}) or {}).items()}
    blocked_nonempty = _sem(prof, "blocked_when_nonempty", [])
    blocked_prefix = _sem(prof, "blocked_when_prefix", {})    # {canonical: prefix}
    chan = _sem(prof, "channel_exclude_name_contains", "")
    bcols = {}
    hdr = rows[prof.header_row]
    for f2 in list(blocked_nonempty) + list(blocked_prefix):
        from .mapping import resolve
        bcols[f2] = resolve(hdr, f2, prof.name)   # blocked rules name RAW headers

    def s(r, j, strip=True, upper=False):
        if j is None or len(r) <= j or r[j] is None:
            return ""
        v = str(r[j])
        if strip:
            v = v.strip()
        return v.upper() if upper else v

    guard_cols = [x for x in (ix["id"], ix.get("name"), ix.get("lat"), ix.get("lon"))
                  if x is not None]
    stores = {}
    for r in rows[prof.start:]:
        if len(r) <= max(guard_cols):
            continue
        shrm = sid(r[ix["id"]])
        if shrm is None:
            continue
        shrm = alias.get(shrm, shrm)
        lat = (_f(r[ix["lat"]]) if ix.get("lat") is not None
               and str(r[ix["lat"]]).strip() else None)
        lon = (_f(r[ix["lon"]]) if ix.get("lon") is not None
               and str(r[ix["lon"]]).strip() else None)
        if lat == 0.0:
            lat = None
        if lon == 0.0:
            lon = None
        st = Store(
            shrm=shrm,
            name=(str(r[ix["name"]]) if ix.get("name") is not None else ""),
            city=(str(r[ix["city"]]) if ix.get("city") is not None
                  and len(r) > ix["city"] else ""),
            state=(str(r[ix["state"]]) if ix.get("state") is not None
                   and len(r) > ix["state"] else ""),
            tier=(str(r[ix["tier"]]) if ix.get("tier") is not None
                  and len(r) > ix["tier"] else ""),
            lat=lat, lon=lon,
            region=s(r, ix.get("region"), upper=True),
            scat=s(r, ix.get("grade"), upper=True),
            model=s(r, ix.get("model"), upper=True),
        )
        st.vw = bool(chan) and chan in st.name.upper()
        st.junk = not (st.name.strip() or st.city.strip())
        st.opened = _dt(r[ix["opened"]]) if ix.get("opened") is not None \
            and len(r) > ix["opened"] else None
        st.l2l = s(r, ix.get("l2l"))
        st.status1 = s(r, ix.get("status"))
        blk = any(s(r, bcols[c]) for c in blocked_nonempty)
        pfx = any(s(r, bcols[c]).startswith(p) for c, p in blocked_prefix.items())
        st.blocked = blk or pfx
        st.stype = s(r, ix.get("store_type"), upper=True)
        st.fmt = s(r, ix.get("format"))
        st.region = st.region.upper()
        stores[shrm] = st
    return stores


# ───────────────────────── holdings (stock file) ─────────────────────────
def excl_reason(shrm, stores, labels, require_attribute_values):
    """Why a stock location is outside the plan universe — None if it plans."""
    st = stores.get(shrm)
    if st is None:
        return labels.get("missing", "not in store master")
    if st.vw:
        return labels.get("channel", "excluded channel store")
    if st.junk:
        return labels.get("junk", "junk master row (no name/city)")
    for attr, allowed in (require_attribute_values or {}).items():
        v = getattr(st, attr, "")
        ok = (v in allowed) if allowed else bool(v)
        if not ok:
            return labels.get("attr_missing", "no {attr} in master").format(attr=attr)
    return None


def load_holdings(path, prof: RoleMapping, stores, *, holding_cls,
                  size_normalizer="numeric_zfill3"):
    """Returns (holdings, excluded, dept_votes)."""
    sid = _id_reader(prof)
    sz = NORMALIZERS[size_normalizer]
    catmap = {}
    for canon, raws in (_sem(prof, "category_values", {}) or {}).items():
        for raw in raws:
            catmap[raw] = canon
    if not catmap:
        raise MappingError(f"{prof.name}: soh profile needs category_values")
    mrp_extended = _sem(prof, "mrp_extended")
    if mrp_extended is None:
        raise MappingError(
            f"{prof.name}: declare mrp_extended true/false — money columns are "
            "presumed extended (unit × row qty) until proven otherwise")
    labels = _sem(prof, "exclusion_labels", {}) or {}
    req_attr = _sem(prof, "require_attribute_values", {}) or {}
    sheets = _sem(prof, "sheets") or [dict(name=prof.sheet, divisions_in=None)]

    holdings = {}
    dept_votes = defaultdict(Counter)
    excluded = defaultdict(lambda: dict(units=0.0, value=0.0, rows=0))
    for spec in sheets:
        rows = read_sheet(path, spec["name"])
        divs = set(spec.get("divisions_in") or []) or None
        ix = prof.index(rows, "division", "location", "category", "size", "style",
                        "qty", "mrp", optional=("value", "season", "dept", "ean"))
        guard = max(v for v in ix.values() if v is not None)
        for r in rows[prof.start:]:
            if len(r) <= guard:
                continue
            div = str(r[ix["division"]])
            if divs is not None and div not in divs:
                continue
            cat = catmap.get(str(r[ix["category"]]))
            if cat is None:
                continue
            q = _f(r[ix["qty"]])
            if q <= 0:
                # OWNER DECISION (2026-08-05): non-positive stock rows are
                # IGNORED — not netted against positives, not ledgered. They
                # are reversal/adjustment noise, not sellable stock; this is
                # the one deliberate exception to the excluded-ledger rule.
                continue
            shrm = sid(r[ix["location"]])
            style = str(r[ix["style"]])
            size = sz(r[ix["size"]])
            if shrm is None:
                continue
            dept = (str(r[ix["dept"]]).strip() if ix.get("dept") is not None else "")
            reason = excl_reason(shrm, stores, labels, req_attr)
            if reason:
                x = excluded[(reason, shrm)]
                x["units"] += q
                x["value"] += _f(r[ix["value"]]) if ix.get("value") is not None else 0.0
                x["rows"] += 1
                continue
            key = (shrm, style)
            hd = holdings.get(key)
            if hd is None:
                hd = holdings[key] = holding_cls(shrm=shrm, style=style, cat=cat, div=div)
            hd.sizes[size] = hd.sizes.get(size, 0.0) + q
            if ix.get("season") is not None:
                hd.seasons[str(r[ix["season"]]).strip()] += q
            mrp_raw = _f(r[ix["mrp"]])
            hd.mrp = max(hd.mrp, (mrp_raw / q if q else 0.0) if mrp_extended else mrp_raw)
            if ix.get("value") is not None:
                hd.value += _f(r[ix["value"]])
            if not hd.retek_dept:
                hd.retek_dept = dept
            if dept:
                dept_votes[style][dept] += q
    return holdings, dict(excluded), dept_votes


# ───────────────────────── norms ─────────────────────────
def load_norms(path, prof: RoleMapping) -> dict:
    sid = _id_reader(prof)
    rows = read_sheet(path, prof.sheet)
    ix = prof.index(rows, "location", "category", "qty", optional=("brand_scope",))
    scope = _sem(prof, "brand_scope_value")
    catmap = {}
    for canon, raws in (_sem(prof, "category_values", {}) or {}).items():
        for raw in raws:
            catmap[raw] = canon
    norms = {}
    guard = max(v for v in ix.values() if v is not None)
    for r in rows[prof.start:]:
        if len(r) <= guard:
            continue
        if scope is not None and str(r[ix["brand_scope"]]).strip() != scope:
            continue
        cat = catmap.get(str(r[ix["category"]]).strip())
        if cat is None:
            continue
        shrm = sid(r[ix["location"]])
        q = _f(r[ix["qty"]])
        if shrm is None or q <= 0:
            continue
        norms[(shrm, cat)] = norms.get((shrm, cat), 0.0) + q
    return norms


# ───────────────────────── velocity overlays ─────────────────────────
_BLANK = dict(ros_life=0.0, strr=0.0, sales_life=0.0, dept="", shelf=0.0,
              nsv_life=0.0, nsv8=0.0, inwards=0.0, grn8=0.0)


def load_ros(sources) -> tuple[dict, dict]:
    """sources = ordered [(path, RoleMapping)] — semantics.kind per overlay:
       base          — full record; also builds the style network aggregate
       refresh       — fresher RoS overwrites; CREATES records data the base
                       never had (they'd otherwise fall through to proxy)
       authoritative — the last word on velocity; negative RoS clamps to 0;
                       sales/GRN evidence lands in <prefix>* fields kept
                       separate from the base sales that drive liveness"""
    perf = {}
    style_net = defaultdict(lambda: {"sales8": 0.0, "sales_life": 0.0,
                                     "ros8_sum": 0.0, "n": 0})
    ev_prefixes = []
    for path, prof in sources:
        sid = _id_reader(prof)
        kind = _sem(prof, "kind", "base")
        rows = read_sheet(path, prof.sheet)
        if kind == "base":
            ix = prof.index(rows, "location", "style", "ros8", "sales8",
                            optional=("ros_life", "str_life", "sales_life", "dept",
                                      "shelf_life", "nsv_life", "nsv8", "inwards", "grn8"))
            guard = max(v for v in ix.values() if v is not None)

            def g(r, f2, ix=ix):
                return _f(r[ix[f2]]) if ix.get(f2) is not None else 0.0

            for r in rows[prof.start:]:
                if len(r) <= guard:
                    continue
                shrm = sid(r[ix["location"]])
                style = str(r[ix["style"]])
                if shrm is None:
                    continue
                rec = dict(ros8=g(r, "ros8"), ros4=0.0, sales8=g(r, "sales8"),
                           ros_life=g(r, "ros_life"), strr=g(r, "str_life"),
                           sales_life=g(r, "sales_life"),
                           dept=(str(r[ix["dept"]]).strip()
                                 if ix.get("dept") is not None else ""),
                           shelf=g(r, "shelf_life"), nsv_life=g(r, "nsv_life"),
                           nsv8=g(r, "nsv8"), inwards=g(r, "inwards"), grn8=g(r, "grn8"))
                perf[(shrm, style)] = rec
                sn = style_net[style]
                sn["sales8"] += rec["sales8"]
                sn["sales_life"] += rec["sales_life"]
                sn["ros8_sum"] += rec["ros8"]
                sn["n"] += 1
        elif kind == "refresh":
            ix = prof.index(rows, "location", "style", "ros8", "ros4")
            guard = max(ix.values())
            for r in rows[prof.start:]:
                if len(r) <= guard:
                    continue
                shrm = sid(r[ix["location"]])
                style = str(r[ix["style"]]).strip()
                if shrm is None or not style:
                    continue
                rec = perf.get((shrm, style))
                if rec is None:
                    rec = dict(ros8=0.0, ros4=0.0, sales8=0.0, **_BLANK)
                    perf[(shrm, style)] = rec
                rec["ros8"], rec["ros4"] = _f(r[ix["ros8"]]), _f(r[ix["ros4"]])
        elif kind == "authoritative":
            pre = _sem(prof, "evidence_prefix", "ev_")
            ev_prefixes.append(pre)
            flag = pre.rstrip("_")
            clamp = _sem(prof, "clamp_negative_ros", True)
            for rec in perf.values():
                rec[f"{pre}sold8"] = 0.0
                rec[f"{pre}sold4"] = 0.0
                rec[f"{pre}grn8"] = 0.0
                rec[flag] = False
            ix = prof.index(rows, "location", "style", "ros8", "ros4",
                            "sales8", "sales4", "grn8")
            guard = max(ix.values())
            for r in rows[prof.start:]:
                if len(r) <= guard:
                    continue
                shrm = sid(r[ix["location"]])
                style = str(r[ix["style"]]).strip()
                if shrm is None or not style:
                    continue
                r8, r4 = _f(r[ix["ros8"]]), _f(r[ix["ros4"]])
                if clamp:
                    r8, r4 = max(0.0, r8), max(0.0, r4)
                rec = perf.get((shrm, style))
                if rec is None:
                    rec = dict(ros8=0.0, ros4=0.0, sales8=0.0, **_BLANK)
                    rec[f"{pre}sold8"] = 0.0
                    rec[f"{pre}sold4"] = 0.0
                    rec[f"{pre}grn8"] = 0.0
                    rec[flag] = False
                    perf[(shrm, style)] = rec
                rec["ros8"], rec["ros4"] = r8, r4
                rec[f"{pre}sold8"] = _f(r[ix["sales8"]])
                rec[f"{pre}sold4"] = _f(r[ix["sales4"]])
                rec[f"{pre}grn8"] = _f(r[ix["grn8"]])
                rec[flag] = True
        else:
            raise MappingError(f"{prof.name}: unknown ros overlay kind {kind!r}")
    return perf, style_net


# ───────────────────────── warehouse free stock ─────────────────────────
def load_fs(path, tot_prof: RoleMapping, split_prof: RoleMapping, *,
            all_sizes, size_normalizer="numeric_zfill3", fallback_plants=()):
    """Totals sheet is authoritative for quantities; the split sheet only
    supplies the warehouse ratio — (style,size) split → style split → overall,
    equal split across fallback_plants when the split sheet is empty."""
    sz = NORMALIZERS[size_normalizer]
    rows = read_sheet(path, tot_prof.sheet)
    hb = rows[tot_prof.header_row]
    term = _sem(tot_prof, "terminator_header", "Grand Total")
    gt = [str(h).strip() for h in hb].index(term)
    allsz = set(all_sizes)
    size_cols = [(j, sz(h)) for j, h in enumerate(hb) if j < gt and sz(h) in allsz]
    style_j = _sem(tot_prof, "style_col_index", 0)
    mi = tot_prof.index(rows, optional=("last_inward", "dept", "category",
                                        "season", "mrp"))
    snb_fs, snb_meta = defaultdict(dict), {}
    for r in rows[tot_prof.start:]:
        if len(r) <= gt:
            continue
        style = str(r[style_j]).strip()
        if not style:
            continue
        for j, s2 in size_cols:
            q = _f(r[j]) if len(r) > j else 0.0
            if q > 0:
                snb_fs[style][s2] = snb_fs[style].get(s2, 0.0) + q
        snb_meta[style] = dict(
            cat=(str(r[mi["category"]]).title() if mi.get("category") is not None else ""),
            dept=(str(r[mi["dept"]]) if mi.get("dept") is not None else ""),
            season=(str(r[mi["season"]]) if mi.get("season") is not None else ""),
            mrp=(_f(r[mi["mrp"]]) if mi.get("mrp") is not None else 0.0),
            last_inward=(str(r[mi["last_inward"]]) if mi.get("last_inward") is not None
                         else ""))

    fd = read_sheet(path, split_prof.sheet)
    ix = split_prof.index(fd, "style", "plant", "size", "qty", "brand", "category")
    cat_ok = set(_sem(split_prof, "category_titlecase_in", []) or [])
    brand_ok = set(_sem(split_prof, "brand_in", []) or [])
    data_ss = defaultdict(lambda: defaultdict(float))
    style_plant = defaultdict(lambda: defaultdict(float))
    tot_by_plant = defaultdict(float)
    guard = max(ix.values())
    for r in fd[split_prof.start:]:
        if len(r) <= guard:
            continue
        if cat_ok and str(r[ix["category"]]).title() not in cat_ok:
            continue
        if brand_ok and str(r[ix["brand"]]) not in brand_ok:
            continue
        q = _f(r[ix["qty"]])
        if q <= 0:
            continue
        style = str(r[ix["style"]])
        size = sz(r[ix["size"]])
        plant = str(r[ix["plant"]])
        data_ss[(style, size)][plant] += q
        style_plant[style][plant] += q
        tot_by_plant[plant] += q
    ov_tot = sum(tot_by_plant.values()) or 1.0
    overall = ({p: tot_by_plant[p] / ov_tot for p in tot_by_plant}
               or {p: 1.0 / len(fallback_plants) for p in fallback_plants})

    fs = defaultdict(lambda: defaultdict(float))
    fs_meta = {}
    for style, szmap in snb_fs.items():
        for size, q in szmap.items():
            split = data_ss.get((style, size))
            if split and sum(split.values()) > 0:
                tot = sum(split.values())
                for p, pq in split.items():
                    fs[(style, size)][p] += q * (pq / tot)
            elif style_plant.get(style):
                sp = style_plant[style]
                tot = sum(sp.values())
                for p, pq in sp.items():
                    fs[(style, size)][p] += q * (pq / tot)
            else:
                for p, rt in overall.items():
                    fs[(style, size)][p] += q * rt
        fs_meta[style] = dict(snb_meta.get(style, {}), brand="")
    return fs, fs_meta


# ───────────────────────── tiers ─────────────────────────
def load_tiers(path, prof: RoleMapping, overrides=None) -> dict:
    sid = _id_reader(prof)
    rows = read_sheet(path, prof.sheet)
    ix = prof.index(rows, "location", "tier")
    tiers = {}
    guard = max(ix.values())
    for r in rows[prof.start:]:
        if len(r) <= guard:
            continue
        shrm = sid(r[ix["location"]])
        t = str(r[ix["tier"]]).strip()
        if shrm is None or not t:
            continue
        tiers[shrm] = t
    for shrm, t in (overrides or {}).items():
        tiers.setdefault(store_id(shrm), t)     # the clustering file wins if present
    return tiers
