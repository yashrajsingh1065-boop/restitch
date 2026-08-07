"""Domain model: stores, holdings, classification, distance.

Everything here is pure vocabulary and math — no file formats, no tenant facts,
no lever values. Functions that depend on a lever take it as a parameter; the
`Policy` object (core.policy) is the single carrier.

Lineage: lifted from a production cut-size redeployment engine (18 days of
buyer-directed evolution, 49 commits). Comments preserve the design rationale;
tenant specifics live outside this package.
"""
from __future__ import annotations

import datetime
import math
import re
from collections import Counter
from dataclasses import dataclass, field


# ───────────────────────── size helpers ─────────────────────────
def normalize_size_numeric3(x) -> str:
    """Numeric ladders (e.g. jacket drops): '96' → '096'. Non-numeric passes through."""
    s = str(x).strip()
    return s.zfill(3) if s.isdigit() else s


def normalize_size_uk_shoe(x) -> str:
    """Shoe ladders with half sizes: 10 / '10.0' → '10', 10.5 → '10.5',
    '6,5' → '6.5'. The half-size decimal is load-bearing — a folding step that
    strips it merges two real sizes. Non-numeric passes through trim+upper."""
    s = str(x).strip().replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return s.upper()
    return str(int(v)) if v == int(v) else f"{v:g}"


def normalize_size_alpha(x) -> str:
    """Alpha ladders (XS/S/M/L/XL/…): trim + uppercase."""
    return str(x).strip().upper()


# ───────────────────────── geography ─────────────────────────
def haversine(a_lat, a_lon, b_lat, b_lon) -> float:
    R = 6371.0
    dlat, dlon = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def canon_city(raw, aliases: dict[str, str] | None = None) -> str:
    """One city, one spelling: trim / uppercase / collapse whitespace, then the
    tenant alias table (e.g. BENGALURU → BANGALORE). Exact-string city equality
    is used by the same-city split, distance tiers and geo scopes — two spellings
    of one city would silently read as two cities."""
    c = re.sub(r"\s+", " ", str(raw).strip().upper())
    return (aliases or {}).get(c, c)


def pair_km(stores, a, b, tier_km: dict[str, float], city_aliases=None) -> float:
    """Store-to-store shipping distance: haversine where both doors carry
    coordinates, city/state/region tier proxy otherwise. THE one distance
    definition — the localization phase, its build check and the independent
    verifier all call this shape."""
    sa, sb = stores[a], stores[b]
    if sa.has_geo and sb.has_geo:
        return haversine(sa.lat, sa.lon, sb.lat, sb.lon)
    ca, cb = canon_city(sa.city, city_aliases), canon_city(sb.city, city_aliases)
    if ca and ca == cb:
        return tier_km["city"]
    ta, tb = canon_city(sa.state), canon_city(sb.state)
    if ta and ta == tb:
        return tier_km["state"]
    if sa.region and sa.region == sb.region:
        return tier_km["region"]
    return tier_km["far"]


# ───────────────────────── transfer geography ─────────────────────────
GEO_MODES = ("pan", "intra_region", "intra_state", "intra_city", "city_set", "max_km")

_GEO_ATTR = {"intra_region": "region", "intra_state": "state", "intra_city": "city"}


def make_geo_edge(stores, pol):
    """Build THE transfer-geography legality predicate from config — returns
    geo_ok(src, dst) -> bool.

    One registry, three evaluators: the allocation stages, the Hungarian donor
    re-pairing and the independent verifier all evaluate this one definition.
    A scope enforced in allocation but not in re-pairing would let the
    localization step quietly carry stock out of scope; a scope the verifier
    does not re-derive from config would be an unproven promise.

    A store with NO value for the scoping attribute has no scope: its edges are
    illegal both ways, and its unhealed stock surfaces in the GEO-BLOCKED
    disposition bucket rather than silently planning pan-network."""
    mode = pol.geo_mode
    if mode == "pan":
        return lambda a, b: True
    if mode == "max_km":
        tk = dict(pol.loc_tier_km)
        al = dict(pol.city_aliases)
        mx = pol.geo_max_km
        return lambda a, b: pair_km(stores, a, b, tk, al) <= mx + 1e-9
    al = dict(pol.city_aliases)
    if mode == "city_set":
        cs = {canon_city(c, al) for c in pol.geo_city_set}
        inset = {sh: canon_city(s.city, al) in cs for sh, s in stores.items()}
        return lambda a, b: inset[a] and inset[b]
    attr = _GEO_ATTR[mode]
    vals = {sh: canon_city(getattr(s, attr), al if attr == "city" else None)
            for sh, s in stores.items()}
    return lambda a, b: bool(vals[a]) and vals[a] == vals[b]


# ───────────────────────── data classes ─────────────────────────
@dataclass
class Store:
    shrm: str
    name: str = ""
    city: str = ""
    state: str = ""
    tier: str = ""
    region: str = ""        # optional attribute; feeds distance tiers + geo scope only
    scat: str = ""          # store grade
    model: str = ""         # ownership model (e.g. COCO / FOFO)
    lat: float | None = None
    lon: float | None = None
    vw: bool = False        # excluded channel door — never plans
    junk: bool = False      # master row with no name/city — never plans
    opened: datetime.datetime | None = None
    l2l: str = ""           # like-to-like status vocabulary from the master
    status1: str = ""       # operating status (e.g. Running / Project Phase)
    blocked: bool = False   # any blocking flag set in the master
    stype: str = ""         # store type label
    fmt: str = ""           # format label

    @property
    def has_geo(self):
        return self.lat is not None and self.lon is not None


@dataclass
class Holding:
    shrm: str
    style: str
    cat: str
    div: str
    sizes: dict = field(default_factory=dict)           # size -> qty
    seasons: Counter = field(default_factory=Counter)   # season label -> units
    mrp: float = 0.0        # UNIT price (loaders must divide extended money by row qty)
    value: float = 0.0      # book value as summed from stock rows
    retek_dept: str = ""    # department label from the stock file
    # filled in by classify():
    present_piv: set = field(default_factory=set)
    missing_piv: list = field(default_factory=list)
    units: float = 0.0
    klass: str = ""         # Complete / Repairable / Broken
    cut: bool = False
    seller: bool = False
    proxy: bool = False     # velocity is a proxy (no data either way)
    ros: float = 0.0        # recent-window RoS, short-window fallback applied
    ros4: float = 0.0
    sales8: float = 0.0
    fresh: bool = False     # current-season stock — protected from all rotation


# ───────────────────────── liveness — ONE definition ─────────────────────────
def eff_ros(ros8, ros4) -> float:
    """Use the 8-week RoS; where there is none, fall back to 4-week. A style with
    no 8-week sale but a 4-week sale is WAKING UP, not dead — the whole point of
    the fallback is that it must not be treated as donor stock."""
    return ros8 if ros8 > 0 else ros4


def is_dead(h: Holding) -> bool:
    """No sale in the recent window (a proxy holding is never 'dead' — no data).

    Reads RoS as well as sales, and MUST keep doing so: a velocity overlay that
    carries RoS but no sales column leaves sales8 == 0 by construction — testing
    sales alone once declared a RoS-1.45 holding deadweight (real defect, caught
    by the heal-completion invariant). Fresh-season stock is never dead: it has
    only just landed and has not had its window."""
    if h.fresh:
        return False
    return (not h.proxy) and h.sales8 == 0 and h.ros == 0


def donor_ok(h: Holding) -> bool:
    """May this holding DONATE pivotal units to the pool?
       Hard rules: fresh never rotates · Complete sets NEVER fragment-donate
       (a dead Complete relocates WHOLE) · Broken fragments always donate ·
       Repairable only if dead — judged by the SAME liveness test as is_dead()."""
    if h.fresh:
        return False
    if h.klass == "Complete":
        return False
    if h.klass == "Broken":
        return True
    if h.proxy:
        return False
    return h.sales8 == 0 and h.ros == 0


# ───────────────────────── season age ─────────────────────────
def make_season_age(pattern: str, spring_label: str, current_index: int):
    """Season-age parser factory: returns seasons-ago index (0 = fresh/current),
    None if unparseable. `pattern` must capture (season-half label, 4-digit year);
    a half matching `spring_label` is the first half of the year."""
    rx = re.compile(pattern)

    def season_age(s):
        m = rx.search(str(s))
        if not m:
            return None
        ss = m.group(1) == spring_label
        y = int(m.group(2))
        return current_index - (y * 2 + (0 if ss else 1))

    return season_age


# ───────────────────────── classification ─────────────────────────
def classify(holdings, perf, *, pivotals, ros_seller, protect_fresh_season, season_age):
    """Fill each holding's analysis fields: set completeness against `pivotals`,
    seller/proxy status against the velocity records, freshness via `season_age`."""
    for (shrm, style), hd in holdings.items():
        hd.units = sum(hd.sizes.values())
        hd.present_piv = {s for s in pivotals if hd.sizes.get(s, 0) > 0}
        hd.missing_piv = [s for s in pivotals if hd.sizes.get(s, 0) <= 0]
        nmiss = len(hd.missing_piv)
        hd.cut = nmiss >= 1
        hd.klass = "Complete" if nmiss == 0 else ("Repairable" if nmiss <= 2 else "Broken")
        pr = perf.get((shrm, style))
        if pr is None:      # no velocity either way -> proxy
            hd.proxy = True
            hd.seller = True    # holdings-only assumption: a store holding it wants to sell it
            hd.ros = 0.0
            hd.ros4 = 0.0
            hd.sales8 = 0.0
        else:
            hd.ros4 = pr["ros4"]
            hd.ros = eff_ros(pr["ros8"], pr["ros4"])
            hd.sales8 = pr["sales8"]
            hd.seller = (pr["sales8"] > 0) or (hd.ros >= ros_seller) or (hd.ros > 0)
        hd.fresh = protect_fresh_season and any(
            season_age(s) == 0 for s, q in hd.seasons.items() if q > 0)


def dept_map(dept_votes, perf):
    """style -> department. Primary: the stock file's department votes (dominant-
    by-units on collisions). Fallback: the velocity file's department for styles
    absent from stock (sold out everywhere — still needed for dept sales history)."""
    m, collisions = {}, 0
    for style, votes in dept_votes.items():
        if len(votes) > 1:
            collisions += 1
        m[style] = votes.most_common(1)[0][0]
    for (_shrm, style), rec in perf.items():
        if style not in m and rec.get("dept"):
            m[style] = rec["dept"]
    return m, collisions


# ───────────────────────── engine inputs ─────────────────────────
@dataclass
class EngineInputs:
    """Everything the engine consumes, already loaded. The engine knows nothing
    about files — loaders (or a compat harness) produce this."""
    stores: dict                    # shrm -> Store
    tiers: dict                     # shrm -> tier label (report bands only)
    holdings: dict                  # (shrm, style) -> Holding  (pre-classify)
    excluded: dict                  # (reason, shrm) -> {units, value, rows}
    dept_votes: dict                # style -> Counter(dept -> units)
    perf: dict                      # (shrm, style) -> velocity record
    style_net: dict                 # style -> network aggregates
    norms: dict                     # (shrm, category) -> norm qty
    norms_context: dict             # (shrm, category) -> context norm qty (report-only)
    fs: dict                        # (style, size) -> {warehouse -> qty} (report-only)
    fs_meta: dict                   # style -> {mrp, cat, ...}
