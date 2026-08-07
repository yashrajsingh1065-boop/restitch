"""Policy — every lever of the engine in one frozen, validated object.

Three kinds of field live here for now (they split into category-profile /
tenant-facts / levers in a later phase, but the values already carry their
lifetimes in the section comments):

  · vocabulary  — size ladder, pivotal sets, season parsing
  · tenant      — frozen style/door lists (EMPTY by default; a tenant overlay
                  supplies them; the public tree never contains real codes)
  · levers      — floors, caps, gates, budgets, toggles

NOT configurable, by design (correctness structures, not preferences):
  · phase ordering — heals reach fixpoint before any capped chosen program
  · transactional all-or-nothing takes with pending ledgers
  · the single mutation point (Ledger.move)
  · checks block the build

Every constraint here was priced on real data during the source project's
evolution; where a default is a judgment call the docstring says what it cost.
"""
from __future__ import annotations

import datetime
import difflib
from dataclasses import dataclass, fields, replace

from .model import GEO_MODES

# Program vocabulary — structure, not configuration.
PROGRAMS = ("HEAL", "SET", "HEAL3", "ASM", "TOPUP", "EXT")
PRIO = {"HEAL": 0, "SET": 1, "HEAL3": 2, "ASM": 3, "TOPUP": 4, "EXT": 5}
HEAL_PROGRAMS = ("HEAL", "HEAL3")   # own-set completions: uplift bar waived, cap-exempt


@dataclass(frozen=True, slots=True)
class Policy:
    # ── vocabulary (category profile tier) ──────────────────────────────
    all_sizes: tuple = ()
    """Canonical size vocabulary. Scenario-INDEPENDENT: loaders keep reading every
    size regardless of which count as pivotal — a de-pivotalled size becomes
    secondary stock, not invisible stock."""
    size_order: tuple = ()
    """Display order for size columns."""
    pivotals: tuple = ()
    """Absence of any one of these ⇒ the holding is CUT. Minimum 2."""
    secondary: tuple = ()
    """Real stock, not set-defining."""
    wave2_pivotals: tuple = ()
    """The wave-2 availability subset (strict subset of pivotals). Empty disables
    wave 2 AND assembly — both are meaningless without a subset lens."""
    ext_lo_sizes: tuple = ()
    """Lower-extreme sizes, NAMED — ladder members, not numeric thresholds
    (a threshold cannot classify an alpha or half-size shoe ladder). A size in
    neither extreme set loads, renders and counts, but never EXT-moves —
    unknown sizes default to frozen, not to a guess."""
    ext_hi_sizes: tuple = ()
    """Higher-extreme sizes, NAMED."""
    depth_fill: int = 1
    """Units injected per missing pivotal to complete a set."""
    season_pattern: str = r"(Spring Summer|Autumn Winter)\s+(\d{4})"
    """Regex over the stock file's season strings; group 1 = half label, group 2 = year."""
    season_spring_label: str = "Spring Summer"
    season_current_index: int = 0
    """Current season as year*2 + (0 spring / 1 autumn). Derived from the stock
    as-of date by the manifest layer; a tenant preset may pin it."""
    season_autumn_start_month: int = 7
    """Month (1-12) in which the autumn half begins — drives the manifest
    layer's season_current_index derivation from the stock as-of date."""

    # ── tenant facts (overlay tier — EMPTY here, real codes never land in this tree) ──
    core_styles: frozenset = frozenset()
    """Frozen stylecodes: never move — not as donors, heal targets, relocations,
    top-ups or extreme moves. Their gaps belong to auto-replenishment."""
    style_variant_suffixes: tuple = ("Q",)
    """Stock files may carry base + suffixed forms of one style; freeze matching
    folds the suffix."""
    afs_stores: frozenset = frozenset()
    """Doors frozen both ways at store grain."""
    foco_frozen: frozenset = frozenset()
    """Named ownership-model doors frozen both ways."""
    fs_warehouses: tuple = ()
    """Warehouse codes appearing in the free-stock file (report-only)."""
    city_aliases: tuple = ()
    """(raw, canonical) city spellings, applied after trim/upper/collapse.
    Exact-string city equality feeds the distance tiers and the geo scopes —
    two spellings of one city would silently read as two cities."""
    franchise_models: tuple = ("FOFO",)
    """Ownership-model labels (master vocabulary) treated as franchise doors —
    the net-flow rule and tighter transit floor apply to these."""
    new_door_l2l: tuple = ("New", "ANN")
    """Master status labels that mark a door NEW when it carries no opening
    date — the new-door donor freeze reads these."""

    # ── levers: budget ──────────────────────────────────────────────────
    movement_budget: int = 25000
    """Units delivered in P1; every unit costs exactly 1 (flat)."""
    budget_p2_extra: int = 5000
    """P2 = the next tranche beyond P1; everything else is P3."""

    # ── levers: floors & caps ───────────────────────────────────────────
    fill_floor: float = 0.80
    """END-state fill floor vs norm — per category AND combined. Flooring only the
    combined number once let 103 doors drop below the per-category floor while the
    total held: the constraint must bind at every grain the sheets display."""
    transit_floor: float = 0.75
    """DURING-movement floor: opening − everything out, nothing landed yet."""
    recv_peak_cap: float = 1.40
    """Receiver PEAK fill cap vs norm for chosen programs."""
    cap_exempt_heals: bool = True
    """A 1-2 u set completion may land even at an over-cap door (the door owns and
    sells the rest of the set). Chosen programs stay capped against TOTAL inbound."""
    cap_exempt_wave2: bool = True
    """Wave-2 heals share the wave-1 heal exemption."""
    style_depth_cap: int = 12
    """No chosen program leaves a door holding more than this of ONE style
    (≈ depth target × pivotals + secondaries). Uncapped, 18 dead sets of one
    style once stacked on a single top-RoS door."""
    one_set_per_door_style: bool = True
    """At most one relocated whole set per (door, style); surplus sets spill to
    the next seller down the RoS ladder."""

    # ── levers: RoS gates ───────────────────────────────────────────────
    ros_seller: float = 0.08
    """Recent-window RoS above which a store is 'selling' the style."""
    min_recv_ros: float = 0.08
    """Absolute bar for a chosen destination to count as a real seller."""
    ros_uplift: float = 1.2
    """Chosen destination must clear uplift × donor RoS — never justified by a
    hair's difference."""

    # ── levers: programs ────────────────────────────────────────────────
    topup_depth_target: int = 2
    """Pivotal replenishment depth at full-set doors; sizes independent; once the
    best door holds the target, leftover distributes down the RoS list."""
    lo_spread: int = 8
    """EXT: units one door absorbs of one style before the next-best door is used.
    Measured sweep (1/2/4/8/16/999): monotone in performance; 8 takes ~85% of the
    gain while still landing extremes in 119 doors rather than 107."""
    assembly_enabled: bool = True
    """Build NEW wave-2-subset sets from the dead leftover pool at doors that never
    carried the style but sell its department."""
    asm_per_door: int = 1
    set_avoid_heal_dest: bool = True
    """SET de-stack: a healed door keeps just its completion; the relocated set
    goes to the next legal seller (stacking recorded as no-alternative fallback)."""
    max_rounds: int = 6
    """Allocation rounds per phase; loops exit early at fixpoint."""

    # ── levers: donor eligibility ───────────────────────────────────────
    protect_selling_fragments: bool = True
    """Broken-but-selling holdings keep 1 unit of each pivotal they hold."""
    protect_fresh_season: bool = True
    """Current-season stock is never rotated — it has just landed."""
    allow_partial_fill: bool = False
    """All-or-nothing set completion. Changing this is NOT a lever — partial fill
    would need its own invariants; kept for visibility only."""
    new_door_donor_freeze: bool = True
    new_door_cutoff: datetime.datetime | None = None
    """Doors opened on/after this never donate (they may receive). Derived as
    stock-as-of minus new_door_window_days by the manifest layer."""
    new_door_window_days: int = 365
    evac_no_norm: bool = True
    """No-norm doors are one-way: never receive; useful stock evacuates to normed
    doors (frozen styles and fresh stock stay frozen even there)."""

    # ── levers: franchise (FOFO) ────────────────────────────────────────
    fofo_net_flow: bool = True
    """A franchise door receives at least as much as it gives — headroom consulted
    live, with allocation rounds so its supply unlocks as inbound accrues."""
    fofo_transit_floor: float = 0.80
    """Tighter during-movement floor for franchise doors."""

    # ── levers: localization ────────────────────────────────────────────
    localize_donors: bool = True
    """Hungarian donor re-pairing within (program-class, style, size, qty) groups —
    pure re-pairing, positions untouched, never lengthens the plan."""
    loc_tier_km: tuple = (("city", 15.0), ("state", 400.0), ("region", 1000.0), ("far", 2000.0))
    """Distance proxy when a coordinate is missing (stored as items; see tier_km)."""

    # ── levers: transfer geography ──────────────────────────────────────
    geo_mode: str = "pan"
    """Transfer geography: pan · intra_region · intra_state · intra_city ·
    city_set · max_km. Enforced as EDGE legality in allocation, localization
    AND verify — one predicate (model.make_geo_edge), three evaluators.
    Constraining geography always costs sets; the run report prices the scope
    by a deterministic re-run with geography open, never by counting blocked
    first-gate attempts (they overstate the unlock)."""
    geo_city_set: tuple = ()
    """city_set mode: transfers are legal only between doors whose canonical
    city is in this set."""
    geo_max_km: float = 0.0
    """max_km mode: an edge is legal iff pair_km(src, dst) is within this."""

    # ── derived views ───────────────────────────────────────────────────
    @property
    def tier_km(self) -> dict:
        return dict(self.loc_tier_km)

    def is_core(self, style) -> bool:
        """Frozen-style match: base code or suffixed variant."""
        s = str(style).strip().upper()
        if s in self.core_styles:
            return True
        return any(s.endswith(suf) and s[: -len(suf)] in self.core_styles
                   for suf in self.style_variant_suffixes if suf)

    def __post_init__(self):
        # sizes are STRINGS — loaders normalize every stock size to str, so an
        # int-typed vocabulary (an unquoted YAML ladder) would match nothing
        # and produce a silently near-empty plan that proves itself perfectly
        # (review M3). from_dict coerces; hand-built policies fail loudly.
        for fname in ("all_sizes", "size_order", "pivotals", "secondary",
                      "wave2_pivotals", "ext_lo_sizes", "ext_hi_sizes"):
            bad = [z for z in getattr(self, fname) if not isinstance(z, str)]
            if bad:
                raise ValueError(
                    f"{fname} entries must be strings (loaders normalize sizes "
                    f"to str; {bad[:3]!r} would match no stock)")
        # store-id sets follow the canonical id law (core.ids): an
        # int-built tenant list would silently match no str-keyed holding —
        # the same M11 failure class as int sizes, from the policy side
        from .ids import store_id
        for fname in ("afs_stores", "foco_frozen"):
            vals = getattr(self, fname)
            canon = frozenset(store_id(x) for x in vals)
            if canon != vals:
                object.__setattr__(self, fname, canon)
        piv, lad = set(self.pivotals), list(self.all_sizes)
        if len(self.pivotals) < 2:
            raise ValueError(f"need >=2 pivotals to define a set, got {self.pivotals}")
        if lad and not piv <= set(lad):
            raise ValueError(f"pivotals {sorted(piv - set(lad))} not in the size ladder")
        w2 = set(self.wave2_pivotals)
        if w2 and not w2 < piv:
            raise ValueError("wave2_pivotals must be a strict subset of pivotals")
        ext = set(self.ext_lo_sizes) | set(self.ext_hi_sizes)
        if ext & piv:
            raise ValueError(f"extreme sizes {sorted(ext & piv)} are pivotals — "
                             "a set-defining size cannot be an extreme")
        if not 0 <= self.transit_floor <= self.fill_floor <= 2:
            raise ValueError("need 0 <= transit_floor <= fill_floor <= 2")
        if self.recv_peak_cap < 1.0:
            raise ValueError("recv_peak_cap below 1.0 would forbid receiving at any door")
        if self.ros_uplift < 1.0:
            raise ValueError("ros_uplift below 1.0 would justify downhill transfers")
        if self.fofo_transit_floor < self.transit_floor:
            raise ValueError("fofo_transit_floor must not be looser than transit_floor")
        if self.movement_budget < 0 or self.budget_p2_extra < 0:
            raise ValueError("budgets must be non-negative")
        if self.assembly_enabled and not self.wave2_pivotals:
            raise ValueError("assembly needs a wave2_pivotals subset (or disable assembly)")
        for k in ("city", "state", "region", "far"):
            if k not in self.tier_km:
                raise ValueError(f"loc_tier_km missing tier '{k}'")
        if not 1 <= self.season_autumn_start_month <= 12:
            raise ValueError("season_autumn_start_month must be a month 1-12")
        if self.geo_mode not in GEO_MODES:
            raise ValueError(f"geo_mode {self.geo_mode!r} not one of {GEO_MODES}")
        if self.geo_mode == "city_set" and not self.geo_city_set:
            raise ValueError("geo_mode 'city_set' needs a non-empty geo_city_set")
        if self.geo_mode == "max_km" and self.geo_max_km <= 0:
            raise ValueError("geo_mode 'max_km' needs geo_max_km > 0")
        for pair in self.city_aliases:
            if len(tuple(pair)) != 2:
                raise ValueError(
                    f"city_aliases entries must be (raw, canonical) pairs, got {pair!r}")
        raws = [str(p[0]) for p in self.city_aliases]
        if len(raws) != len(set(raws)):
            dupe = sorted({r for r in raws if raws.count(r) > 1})
            raise ValueError(
                f"city_aliases raw keys duplicated {dupe}: last-wins would make "
                "the provenance round-trip unfaithful")

    def with_(self, **kw) -> Policy:
        """dataclasses.replace with validation re-run."""
        return replace(self, **kw)


# ── YAML round-trip ─────────────────────────────────────────────────────
_TUPLE_FIELDS = {f.name for f in fields(Policy) if f.type == "tuple"}
_FSET_FIELDS = {f.name for f in fields(Policy) if f.type == "frozenset"}


def from_dict(d: dict) -> Policy:
    """Build a Policy from plain YAML/JSON data with loud unknown-key rejection.
    Lists coerce to tuples / frozensets; loc_tier_km accepts a mapping;
    new_door_cutoff accepts an ISO date string or date."""
    known = {f.name for f in fields(Policy)}
    extra = sorted(set(d) - known)
    if extra:
        near = {k: difflib.get_close_matches(k, sorted(known), n=1) for k in extra}
        hints = ", ".join(f"{k!r}" + (f" (did you mean {m[0]!r}?)" if m else "")
                          for k, m in near.items())
        raise ValueError(f"unknown policy keys: {hints}")
    kw = dict(d)
    _SIZE_FIELDS = ("all_sizes", "size_order", "pivotals", "secondary",
                    "wave2_pivotals", "ext_lo_sizes", "ext_hi_sizes")
    for k in _TUPLE_FIELDS & set(kw):
        if k in ("loc_tier_km", "city_aliases") and isinstance(kw[k], dict):
            kw[k] = tuple(kw[k].items())
        elif k in _SIZE_FIELDS:
            # unquoted YAML sizes arrive as ints/floats; the vocabulary is str
            kw[k] = tuple(str(x) for x in kw[k])
        else:
            kw[k] = tuple(tuple(x) if isinstance(x, list) else x for x in kw[k])
    for k in _FSET_FIELDS & set(kw):
        kw[k] = frozenset(kw[k])
    v = kw.get("new_door_cutoff")
    if isinstance(v, str):
        kw["new_door_cutoff"] = datetime.datetime.fromisoformat(v)
    elif isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
        kw["new_door_cutoff"] = datetime.datetime(v.year, v.month, v.day)
    return Policy(**kw)


def to_dict(pol: Policy) -> dict:
    """Plain-data view of a Policy — YAML/JSON safe, round-trips via from_dict.
    This is the resolved-config record stamped into every run's provenance."""
    out = {}
    for f in fields(Policy):
        v = getattr(pol, f.name)
        if isinstance(v, frozenset):
            v = sorted(v)
        elif f.name in ("loc_tier_km", "city_aliases"):
            v = dict(v)
        elif isinstance(v, tuple):
            v = list(v)
        elif isinstance(v, datetime.datetime):
            v = v.isoformat()
        out[f.name] = v
    return out


def band_atoms(units_in_rank_order, budget: float, p2_extra: float) -> list:
    """Windowed-greedy banding — THE one banding law, shared by the engine and
    the battery's budget_greedy check (review M1: the check once encoded a
    stricter law the engine never promised and refused 73 legitimate plans).

    Atoms fund in rank order. An atom that no longer fits the remaining window
    defers WITHOUT consuming it, so a smaller later atom may fill the gap
    (skip-fill): budget use is maximized, and a deferred atom outranking a
    funded one is exactly the recorded consequence of not fitting."""
    bands, cum = [], 0.0
    for u in units_in_rank_order:
        if cum + u <= budget:
            band = "P1"
        elif cum + u <= budget + p2_extra:
            band = "P2"
        else:
            band = "P3"
        if band != "P3":
            cum += u
        bands.append(band)
    return bands


def permissive(base: Policy) -> Policy:
    """Every guard off — the costing ceiling. Run it to see what the constraints
    cost, then justify each one against that number."""
    return base.with_(
        fill_floor=0.0, transit_floor=0.0, fofo_transit_floor=0.0,
        recv_peak_cap=10**6, style_depth_cap=10**9,
        min_recv_ros=0.0, ros_uplift=1.0,
        core_styles=frozenset(), afs_stores=frozenset(), foco_frozen=frozenset(),
        new_door_donor_freeze=False, fofo_net_flow=False,
        movement_budget=10**9,
        geo_mode="pan", geo_city_set=(), geo_max_km=0.0,
    )
