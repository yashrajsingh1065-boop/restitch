"""The lever panel spec — every buyer-adjustable Policy field, grouped, with
the one-line cost note from the source project's measured history.

Levers only: vocabulary (ladders, pivotals) and tenant facts (frozen lists)
belong to the profile and preset, not to a run-time panel. Every lever shows
value · preset default · what changing it cost when it was actually measured.
No naked toggles.
"""
from __future__ import annotations

# (field, input kind, label, cost note)
LEVER_GROUPS: tuple = (
    ("Budget & waves", (
        ("movement_budget", "int", "Movement budget (units, P1)",
         "flat pricing: every unit costs exactly 1; sets fund whole or not at all"),
        ("budget_p2_extra", "int", "P2 tranche beyond P1",
         "the next tranche; everything past it is P3, deferred"),
        ("topup_depth_target", "int", "Top-up depth per pivotal",
         "replenishment into full-set doors; leftover spills down the RoS list"),
        ("lo_spread", "int", "Extreme-size spread per door",
         "measured sweep 1/2/4/8/16/999: 8 takes ~85% of the gain while still "
         "landing extremes in 119 doors rather than 107"),
        ("max_rounds", "int", "Allocation rounds per phase",
         "loops exit early at fixpoint; rounds only matter for net-flow unlocks"),
    )),
    ("Floors & caps", (
        ("fill_floor", "float", "End-state fill floor (× norm)",
         "binds per category AND combined: flooring only the total once let 103 "
         "doors drop below a per-category floor while the total held"),
        ("transit_floor", "float", "During-movement floor (× norm)",
         "opening − everything out, nothing landed yet: donors stay sellable "
         "before their inbound arrives"),
        ("fofo_transit_floor", "float", "Franchise during-movement floor",
         "tighter than the network floor; never looser"),
        ("recv_peak_cap", "float", "Receiver peak cap (× norm)",
         "chosen inbound never pushes a door past this, heal arrivals counted"),
        ("style_depth_cap", "int", "Style depth cap (units of one style)",
         "uncapped, 18 dead sets of one style once stacked on a single "
         "top-RoS door"),
    )),
    ("RoS gates", (
        ("ros_seller", "float", "Seller threshold (RoS)",
         "above this a store counts as selling the style"),
        ("min_recv_ros", "float", "Chosen-destination floor (RoS)",
         "a chosen destination must be a real seller, not merely less dead"),
        ("ros_uplift", "float", "Uplift multiple vs donor",
         "never justified by a hair's difference; heals answer a softer bar"),
    )),
    ("Geography", (
        ("geo_mode", "geo", "Transfer geography",
         "constraining geography always costs sets; the report prices the "
         "scope by a deterministic re-run with geography open"),
        ("geo_city_set", "csv", "City set (city_set mode)",
         "transfers legal only between doors whose canonical city is listed"),
        ("geo_max_km", "float", "Max km (max_km mode)",
         "an edge is legal only within this straight-line distance"),
    )),
    ("Toggles", (
        ("localize_donors", "bool", "Donor localization",
         "pure re-pairing, never lengthens the plan; −29% km on the source "
         "project's real data"),
        ("assembly_enabled", "bool", "Assembly at virgin dept-sellers",
         "dead donors only, capped per door; needs a wave-2 subset"),
        ("fofo_net_flow", "bool", "Franchise net flow",
         "a franchise door receives at least as much as it gives"),
        ("evac_no_norm", "bool", "Evacuate no-norm doors",
         "one-way: they donate everything useful, receive nothing"),
        ("protect_fresh_season", "bool", "Protect current season",
         "fresh stock has not had its selling window; it never rotates"),
        ("new_door_donor_freeze", "bool", "New-door donor freeze",
         "doors opened inside the window may receive, never donate"),
        ("cap_exempt_heals", "bool", "Heals exempt from the peak cap",
         "a 1-2 u completion lands even at an over-cap door: it owns the set"),
        ("cap_exempt_wave2", "bool", "Wave-2 heals share the exemption",
         "wave-2 completions follow the wave-1 heal rule"),
        ("set_avoid_heal_dest", "bool", "SET de-stack",
         "one door, one windfall; stacking only as recorded fallback"),
        ("one_set_per_door_style", "bool", "One relocated set per door×style",
         "surplus sets spill to the next seller down the RoS ladder"),
        ("protect_selling_fragments", "bool", "Selling fragments keep a front",
         "broken-but-selling holdings keep 1 unit of each held pivotal"),
    )),
)

GEO_MODES_HELP = {
    "pan": "anywhere to anywhere (open network)",
    "intra_region": "both doors in the same region",
    "intra_state": "both doors in the same state",
    "intra_city": "both doors in the same city",
    "city_set": "both doors inside the named city set",
    "max_km": "within a straight-line distance",
}


def parse_lever(kind: str, raw: str):
    """Form string -> policy value. Empty string means 'keep the preset'."""
    raw = (raw or "").strip()
    if raw == "":
        return None
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        return raw.lower() in ("1", "true", "on", "yes")
    if kind == "csv":
        vals = [v.strip() for v in raw.split(",") if v.strip()]
        return vals or None
    return raw          # geo / plain string


_KIND_WANTS = {"int": "a whole number", "float": "a number"}


def collect_overrides(form, base: dict) -> tuple[dict, dict]:
    """-> (overrides, errors). Changed-from-preset levers only land in
    overrides; a value that does not parse lands in errors NAMED BY ITS LEVER
    LABEL — never as a bare Python exception. Nothing persists on any error."""
    out, errors = {}, {}
    for _group, levers in LEVER_GROUPS:
        for field, kind, label, _note in levers:
            raw = form.get(field)
            if raw is None or (kind != "bool" and str(raw).strip() == ""):
                continue
            try:
                val = parse_lever(kind, raw)
            except ValueError:
                errors[field] = (f"{label} needs "
                                 f"{_KIND_WANTS.get(kind, 'a valid value')} — "
                                 f"got {str(raw).strip()!r}")
                continue
            if val is not None and val != base.get(field):
                out[field] = val
    return out, errors
