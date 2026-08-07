"""The rule registry — every toggleable rule with its whole proof chain.

One rule id owns three proof surfaces:
  · invariants — numbered assertions in core.engine._invariants (run-blocking)
  · check_ids  — build-battery checks rendered with the workbook (build-blocking)
  · verify_ids — independent post-save verifications (replay the artifact)

Toggling a rule toggles its whole chain: a disabled rule's checks are emitted
with skipped=True — "42 checks: 38 passed, 4 skipped" — never silently dropped,
so a shrunk battery can always be told apart from a passing one.

The `doc` strings carry the buyer directives the rules encode. They are the
institutional memory of the source project's 49 commits of rule evolution and
travel with the rule, not with any one engine's comments.

NOT in this registry (correctness structures, not preferences): phase ordering,
transactional all-or-nothing takes, move() as the single mutation point,
checks-block-the-build, and the kernel invariants listed in KERNEL_INVARIANTS.
"""
from __future__ import annotations

from dataclasses import dataclass

from .policy import Policy


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    doc: str
    enabled: object                     # Callable[[Policy], bool]
    invariants: tuple = ()              # numbers in core.engine._invariants
    check_ids: tuple = ()               # build battery (render/checks port)
    verify_ids: tuple = ()              # independent verifier (verify port)


# Kernel invariants owned by NO rule — they hold in every configuration and
# no lever may switch them off. 7 (chosen moves strictly climb the RoS
# gradient) is kernel: every allocation gate enforces strict climb even with
# the uplift lever at its neutral 1.0.
KERNEL_INVARIANTS = (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13)


RULES: tuple = (
    Rule("style_freeze", "Frozen styles never move",
         "Buyer's frozen list (CORE): never donors, heal targets, relocations, "
         "top-ups or extreme moves — base or suffixed variant form. Their gaps "
         "belong to auto-replenishment, not to the plan.",
         lambda p: bool(p.core_styles), invariants=(15,),
         check_ids=("core_freeze",), verify_ids=("v_core",)),
    Rule("door_freeze_afs", "Frozen doors (AFS) touch nothing",
         "Named doors frozen BOTH ways at store grain — neither donate nor "
         "receive.",
         lambda p: bool(p.afs_stores), invariants=(16,),
         check_ids=("afs_freeze",), verify_ids=("v_afs",)),
    Rule("door_freeze_master", "Blocked / not-open / named-model doors are frozen",
         "A master blocking flag, a non-Running status, or membership in the "
         "frozen ownership-model list takes a door out of the plan both ways "
         "(buyer 24-Jul). Data-driven: the master file decides, every run.",
         lambda p: True, invariants=(21,),
         check_ids=("frozen_doors", "eligible_ends"), verify_ids=("v_frozen", "v_in_master")),
    Rule("new_door", "New doors never donate",
         "A door opened inside the window is still building its base stock: it "
         "may receive, it never donates. Cutoff derives from the stock as-of "
         "date, not from a wall clock.",
         lambda p: p.new_door_donor_freeze, invariants=(20,),
         check_ids=("new_door_freeze",), verify_ids=("v_new_door",)),
    Rule("no_norm", "No-norm doors are one-way",
         "A door with no norm is being EVACUATED: it only ever donates — keep-1 "
         "fronts waived, live cut stock enrolled — and never receives. The "
         "no-norm FRANCHISE door is exempt entirely (buyer 24-Jul): someone "
         "else's stock, no basis to plan it.",
         lambda p: p.evac_no_norm, invariants=(17, 26),
         check_ids=("no_norm_one_way", "nnf_exempt"), verify_ids=("v_no_norm_in", "v_nnf")),
    Rule("fresh", "Current-season stock never rotates",
         "Fresh stock has just landed and has not had its selling window — it "
         "is never dead, never a donor, never an extreme.",
         lambda p: p.protect_fresh_season, invariants=(8,),
         check_ids=("fresh_never_donor",), verify_ids=("v_fresh",)),
    Rule("floors", "Fill + transit floors, per category AND combined",
         "END state >= fill_floor of norm and DURING-movement position >= the "
         "transit floor, at every grain the sheets display. Flooring only the "
         "combined number once let 103 doors drop below a per-category floor "
         "while the total held.",
         lambda p: p.fill_floor > 0 or p.transit_floor > 0, invariants=(14, 18),
         check_ids=("fill_floor", "transit_floor"), verify_ids=("v_transit", "v_fill")),
    Rule("peak_cap", "Receiver peak cap on chosen programs",
         "Chosen inbound never pushes a door past recv_peak_cap x norm — wave-1 "
         "strong form counts heal arrivals too. Heal completions may land at an "
         "over-cap door (the door owns and sells the rest of the set).",
         lambda p: p.recv_peak_cap < 10**5, invariants=(18,),
         check_ids=("peak_cap",), verify_ids=("v_peak",)),
    Rule("style_depth", "Style depth cap with spill",
         "No chosen program leaves a door holding more than the cap of ONE "
         "style; surplus spills to the next seller down the RoS ladder. "
         "Uncapped, 18 dead sets of one style once stacked on a single "
         "top-RoS door.",
         lambda p: p.style_depth_cap < 10**9, invariants=(23,),
         check_ids=("style_depth",), verify_ids=("v_style_depth",)),
    Rule("destack", "SET de-stack — one door, one windfall",
         "A door already healed from the pool keeps just its completion; the "
         "relocated whole set goes to the NEXT legal seller. Stacking survives "
         "only as the recorded no-alternative fallback. (No independent verify "
         "surface: the no-alternative record is engine state, invisible in the "
         "saved artifact.)",
         lambda p: p.set_avoid_heal_dest, invariants=(27,), check_ids=("set_destack",)),
    Rule("ros_gates", "RoS confidence gates",
         "Chosen destinations clear min_recv_ros AND uplift x donor RoS — never "
         "justified by a hair's difference; heals never run RoS-downhill.",
         lambda p: p.min_recv_ros > 0 or p.ros_uplift > 1, invariants=(19,),
         check_ids=("ros_gate", "ros_confidence"), verify_ids=("v_ros",)),
    Rule("fofo_net_flow", "Franchise doors are net receivers",
         "A franchise door receives at least as much as it gives — its "
         "headroom is consulted live and its supply unlocks only as inbound "
         "accrues.",
         lambda p: p.fofo_net_flow, invariants=(22,),
         check_ids=("fofo_net", "fofo_floor"), verify_ids=("v_fofo",)),
    Rule("wave2", "Wave-2 subset heals",
         "On the plan's after-position, complete MORE sets under the wave-2 "
         "subset lens — never re-serving a wave-1 receiver, never breaking an "
         "existing subset set at a donor (protect3).",
         lambda p: bool(p.wave2_pivotals), invariants=(24,),
         check_ids=("wave2_replay",), verify_ids=("v_wave2",)),
    Rule("assembly", "Assembled sets at virgin department sellers",
         "Build NEW subset sets from the dead leftover pool at doors that "
         "never carried the style but sell its department — dead donors only, "
         "capped per door, every chosen gate binding.",
         lambda p: p.assembly_enabled and bool(p.wave2_pivotals), invariants=(25,),
         check_ids=("assembly_replay",), verify_ids=("v_assembly",)),
    Rule("geo_scope", "Transfer geography",
         "Every edge is legal under the configured scope — enforced in "
         "allocation, localization and verify from ONE predicate. Out-of-scope "
         "stock surfaces GEO-BLOCKED; the scope's cost is priced by a "
         "deterministic re-run with geography open, never by blocked counts.",
         lambda p: p.geo_mode != "pan", invariants=(29,),
         check_ids=("geo_edges",), verify_ids=("v_geo",)),
    Rule("localize", "Donor localization",
         "Hungarian re-pairing within (program-class, style, size, qty) groups "
         "— pure re-pairing that never lengthens the plan and never picks an "
         "edge the allocation gates (geography included) would refuse.",
         lambda p: p.localize_donors, invariants=(28,),
         check_ids=("localization",), verify_ids=("v_localization",)),
)

_BY_ID = {r.id: r for r in RULES}
assert len(_BY_ID) == len(RULES), "duplicate rule id"


def rule(rule_id: str) -> Rule:
    return _BY_ID[rule_id]


def rule_status(pol: Policy) -> list:
    """[(id, title, enabled)] for every registered rule under this policy."""
    return [(r.id, r.title, bool(r.enabled(pol))) for r in RULES]
