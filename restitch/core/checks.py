"""Check battery mechanics — stable ids, block-the-build, skipped surfacing.

A check id is a stable string (`peak_cap.wave1_strong`), never an ordinal:
ordinals renumber themselves when a check is added and make two reports
incomparable. A disabled rule's checks are EMITTED with skipped=True rather
than dropped — "42 checks: 38 passed, 4 skipped" and a silently shrunk battery
must never look alike.

The build renderer refuses to save a workbook while any check is failed; the
run report shows checks FIRST. This module is the shared frame; the ported
build battery registers its checks here, and the rule registry (core.rules)
declares which ids belong to which rule.
"""
from __future__ import annotations

from dataclasses import dataclass

from .policy import Policy
from .rules import RULES


@dataclass
class Check:
    id: str                 # stable string, "<rule_or_section>.<name>"
    section: str
    name: str
    passed: bool = True
    skipped: bool = False
    detail: str = ""
    warn: bool = False      # amber advisory — reports, never blocks

    @property
    def failed(self) -> bool:
        return not self.passed and not self.skipped and not self.warn


class Battery:
    """Ordered check collection with one summary vocabulary."""

    def __init__(self):
        self.checks: list[Check] = []
        self._ids: set = set()

    def add(self, cid: str, section: str, name: str, passed: bool,
            detail: str = "", warn: bool = False) -> Check:
        if cid in self._ids:
            raise ValueError(f"duplicate check id {cid!r}")
        self._ids.add(cid)
        c = Check(cid, section, name, bool(passed), False, detail, warn)
        self.checks.append(c)
        return c

    def skip(self, cid: str, section: str, name: str, reason: str) -> Check:
        if cid in self._ids:
            raise ValueError(f"duplicate check id {cid!r}")
        self._ids.add(cid)
        c = Check(cid, section, name, True, True, reason)
        self.checks.append(c)
        return c

    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.failed]

    @property
    def ok(self) -> bool:
        return not self.failed()

    def counts(self) -> tuple[int, int, int]:
        p = sum(1 for c in self.checks if c.passed and not c.skipped)
        f = len(self.failed())
        s = sum(1 for c in self.checks if c.skipped)
        return p, f, s

    def warned(self) -> list[Check]:
        return [c for c in self.checks if c.warn and not c.passed and not c.skipped]

    def summary(self) -> str:
        p, f, s = self.counts()
        bits = [f"{len(self.checks)} checks: {p} passed"]
        if f:
            bits.append(f"{f} FAILED")
        if s:
            bits.append(f"{s} skipped")
        return ", ".join(bits)


# ═══════════════════════ the build battery ═══════════════════════
# Ported from the source project's 42-check battery (movement_final.run_checks)
# and generalized: every tenant literal is now config, every check belongs to a
# section, rule-owned checks skip (visibly) when their rule is off, and a new
# geo check joins as the geo_scope rule's check surface. Wherever the quantity
# allows, a check replays the PRODUCED ROWS onto the opening holdings rather
# than trusting engine bookkeeping — the file cannot drift from what was
# verified.

def build_recon(R) -> dict:
    """Per-store and per-holding position, before and after, from the moves alone."""
    from collections import Counter
    H, moves = R["holdings"], R["moves"]
    before, out_u, in_u = Counter(), Counter(), Counter()
    post = {k: dict(h.sizes) for k, h in H.items()}
    for (shrm, _st), h in H.items():
        before[shrm] += h.units
    for m in moves:
        out_u[m["src"]] += m["qty"]
        in_u[m["dst"]] += m["qty"]
        d = post.setdefault((m["src"], m["style"]), {})
        d[m["size"]] = d.get(m["size"], 0) - m["qty"]
        d = post.setdefault((m["dst"], m["style"]), {})
        d[m["size"]] = d.get(m["size"], 0) + m["qty"]
    end = Counter({sh: before[sh] - out_u[sh] + in_u[sh]
                   for sh in set(before) | set(out_u) | set(in_u)})
    return dict(before=before, out=out_u, inn=in_u, end=end, post=post)


# check id -> owning rule id, DERIVED from the registry (absent = kernel,
# never skips). Hand-inverting this once let an id fall through to "kernel"
# and compute while its rule was disabled (review architecture #6).
_CHECK_RULE = {cid: r.id for r in RULES for cid in r.check_ids}


def run_battery(R, rows, recon, pol: Policy) -> Battery:
    """The full build battery over the produced rows. A failed check makes the
    battery block the build (render refuses to save); a disabled rule's checks
    are emitted skipped in place, so the numbering never shifts."""
    from collections import Counter, defaultdict

    from .model import donor_ok, is_dead, make_geo_edge, pair_km
    from .policy import PRIO, band_atoms

    H, stores, norms = R["holdings"], R["stores"], R["norms"]
    PIVOTALS = list(pol.pivotals)
    W2 = tuple(pol.wave2_pivotals)
    is_core = pol.is_core
    b = Battery()
    rules_on = {r.id: bool(r.enabled(pol)) for r in RULES}

    def add(cid, section, name, ok, detail, warn=False):
        owner = _CHECK_RULE.get(cid)
        b.add(cid, section, name, ok, detail, warn=warn)
        assert owner is None or rules_on[owner], \
            f"check {cid} computed while its rule {owner} is disabled"

    def skip_if_off(cid, section, name):
        owner = _CHECK_RULE.get(cid)
        if owner is not None and not rules_on[owner]:
            b.skip(cid, section, name, "rule disabled by policy")
            return True
        return False

    tot_u = sum(x["qty"] for x in rows)
    SEC_A = "A · conservation & ledger"
    SEC_B = "B · movement legality"
    SEC_C = "C · donor & set law"
    SEC_D = "D · budget"
    SEC_E = "E · capacity, floors & flow balance"
    SEC_F = "F · frozen styles & doors"
    SEC_G = "G · RoS gates"
    SEC_H = "H · waves & assembly"
    SEC_I = "I · geography & localization"

    # ── A · conservation & ledger ──
    add("conservation", SEC_A,
        "Conservation — units moved equal units received, nothing created or destroyed",
        abs(sum(recon["out"].values()) - sum(recon["inn"].values())) < 1e-6
        and abs(tot_u - sum(recon["out"].values())) < 1e-6,
        f"{tot_u:,.0f} u out == {sum(recon['inn'].values()):,.0f} u in")

    over = 0
    shipped = Counter()
    for x in rows:
        shipped[(x["src"], x["style"], x["size"])] += x["qty"]
    for (sh, st, sz), q in shipped.items():
        h = H.get((sh, st))
        if h is None or q > h.sizes.get(sz, 0) + 1e-9:
            over += 1
    add("no_overdraw", SEC_A,
        "No donor over-draw — no store ships more of a size than it opened with",
        over == 0, f"{len(shipped):,} ship keys checked · {over} over-drawn")

    neg = sum(1 for szm in recon["post"].values() for q in szm.values() if q < -1e-9)
    add("no_negative", SEC_A,
        "No negative stock — every line applied leaves all positions >= 0",
        neg == 0,
        f"{sum(len(s) for s in recon['post'].values()):,} positions · {neg} negative")

    mism = sum(1 for sh in recon["end"]
               if abs(recon["before"][sh] - recon["out"][sh] + recon["inn"][sh]
                      - recon["end"][sh]) > 1e-6)
    tb, te = sum(recon["before"].values()), sum(recon["end"].values())
    add("per_store_recon", SEC_A,
        "Per-store reconciliation — SOH − out + in = end for every store; network unchanged",
        mism == 0 and abs(tb - te) < 1e-6,
        f"{len(recon['end']):,} stores · {mism} mismatches · network {tb:,.0f} → {te:,.0f}")

    cut_r = tot_r = 0.0
    for (_sh, _st), szm in recon["post"].items():
        u = sum(q for q in szm.values() if q > 0)
        if u <= 0:
            continue
        tot_r += u
        if any(szm.get(p, 0) <= 0 for p in PIVOTALS):
            cut_r += u
    fl = R["fill"]["full"]
    eng_cut = fl["cut"]["ALL"] / fl["tot"]["ALL"] if fl["tot"]["ALL"] else 0.0
    fil_cut = cut_r / tot_r if tot_r else 0.0
    add("end_to_end_cut", SEC_A,
        "END-TO-END — SOH rebuilt from this file reproduces the engine's promised cut%",
        abs(eng_cut - fil_cut) < 1e-6,
        f"rebuilt {fil_cut:.4%} vs engine {eng_cut:.4%} (Δ {fil_cut - eng_cut:+.6%}) · "
        f"{tot_r:,.0f} u")

    # ── B · movement legality ──
    add("no_self_moves", SEC_B, "No self-moves",
        not any(x["src"] == x["dst"] for x in rows),
        f"{len(rows):,} lines · 0 with src == dst")

    ships = {(x["src"], x["style"], x["size"]) for x in rows}
    recvs = {(x["dst"], x["style"], x["size"]) for x in rows}
    rt = ships & recvs
    add("no_round_trips", SEC_B,
        "No round trips — no store both ships and receives the same style+size",
        not rt, f"{len(ships):,} ship × {len(recvs):,} receive keys · {len(rt)} overlap")

    badm = [x for x in rows
            if x["src"] not in stores or x["dst"] not in stores
            or stores[x["src"]].vw or stores[x["dst"]].vw
            or stores[x["src"]].junk or stores[x["dst"]].junk]
    add("real_ends", SEC_B,
        "Both ends are real, included stores — in the master, not excluded, not junk",
        not badm, f"{len(rows):,} lines · {len(badm)} bad ends")

    sbs = R["sb_stores"]
    nos = [x for x in rows if x["src"] not in sbs or x["dst"] not in sbs]
    add("stocked_ends", SEC_B,
        "Both ends hold category stock — the stock file IS the live network",
        not nos,
        f"{len({x['src'] for x in rows} | {x['dst'] for x in rows})} distinct doors · "
        f"{len(nos)} lines with a stockless end")

    add("no_warehouse", SEC_B,
        "Warehouse free stock is NOT in this plan — every end is a store id",
        all(x["src"] in stores and x["dst"] in stores for x in rows),
        f"{len(rows):,} lines · FS reported on its own sheet only")

    badq = [x for x in rows if x["qty"] < 1 or x["qty"] != int(x["qty"])
            or not str(x["style"]).strip() or not str(x["size"]).strip()]
    add("executable_lines", SEC_B,
        "Every line is executable — integer qty >= 1, style and size present",
        not badq, f"{len(badq)} malformed lines")

    ladder = set(pol.all_sizes)
    offlad = sorted({str(x["size"]) for x in rows if x["size"] not in ladder})
    add("sizes_in_ladder", SEC_B,
        "Every moved size belongs to the declared ladder",
        not offlad,
        f"sizes on file: {sorted({str(x['size']) for x in rows})}"
        + (f" · outside the ladder: {offlad}" if offlad else ""), warn=True)

    # ── C · donor & set law ──
    frag_bad = [x for x in rows if x["program"] != "SET"
                and H[(x["src"], x["style"])].klass == "Complete"]
    add("full_set_freeze", SEC_C,
        "FULL-SET FREEZE — no article leaves a full set except as a whole-set relocation",
        not frag_bad,
        f"{len(rows):,} lines · {len(frag_bad)} sourced from a Complete holding outside SET")

    set_bad = 0
    set_pairs = set()
    set_moved, set_dsts = Counter(), defaultdict(set)
    for x in rows:
        if x["program"] != "SET":
            continue
        set_moved[(x["src"], x["style"])] += x["qty"]
        set_dsts[(x["src"], x["style"])].add(x["dst"])
    for (sh, st), q in set_moved.items():
        h = H[(sh, st)]
        if abs(q - h.units) > 1e-9 or len(set_dsts[(sh, st)]) != 1:
            set_bad += 1
        else:
            dst = next(iter(set_dsts[(sh, st)]))
            endp = recon["post"].get((dst, st), {})
            if any(endp.get(p, 0) <= 0 for p in PIVOTALS):
                set_bad += 1
        set_pairs.add((sh, st))
    add("sets_intact", SEC_C,
        "Whole-set relocations move INTACT — entire set, one destination, lands full",
        set_bad == 0, f"{len(set_pairs):,} dead full sets relocated · {set_bad} broken")

    # the donor LAW itself, not a re-implementation of it — the check once
    # tested liveness inline and passed a proxy donor donor_ok() forbids
    src_bad = 0
    for x in rows:
        if x["program"] not in ("HEAL", "HEAL3", "TOPUP", "EXT"):
            continue
        h = H[(x["src"], x["style"])]
        if not (donor_ok(h) or (x["src"] in R["no_norm"]
                                and h.klass != "Complete" and not h.fresh)):
            src_bad += 1
    add("donor_law", SEC_C,
        "Fragment donors satisfy the donor LAW — donor_ok(), or the no-norm "
        "evacuation exception",
        src_bad == 0,
        f"{sum(1 for x in rows if x['program'] != 'SET'):,} fragment lines · "
        f"{src_bad} from a non-donor holding")

    if not skip_if_off("fresh_never_donor", SEC_C,
                       "Current season never a donor"):
        fresh_bad = [x for x in rows if H[(x["src"], x["style"])].fresh]
        add("fresh_never_donor", SEC_C,
            "Current season never a donor — fresh stock has not had its selling window",
            not fresh_bad,
            f"{sum(1 for h in H.values() if h.fresh):,} fresh holdings protected · "
            f"{len(fresh_bad)} violated")

    keep_bad = 0
    for (sh, st), keep in R["frag_keep"].items():
        endp = recon["post"].get((sh, st), {})
        h = H[(sh, st)]
        for p in PIVOTALS:
            if h.sizes.get(p, 0) >= keep and endp.get(p, 0) < keep - 1e-9:
                keep_bad += 1
    add("keep_fronts", SEC_C,
        "Selling fragments keep their front — 1 unit of each held pivotal stays",
        keep_bad == 0,
        f"{len(R['frag_keep']):,} selling-Broken holdings · {keep_bad} stripped")

    heal_bad = 0
    heal_dsts = {(x["dst"], x["style"]) for x in rows if x["program"] == "HEAL"}
    for (dst, st) in heal_dsts:
        endp = recon["post"].get((dst, st), {})
        if any(endp.get(p, 0) <= 0 for p in PIVOTALS):
            heal_bad += 1
    add("heal_completion", SEC_C,
        f"Set completion — every healed door ends holding all {len(PIVOTALS)} pivotal sizes",
        heal_bad == 0, f"{len(heal_dsts):,} healed sets · {heal_bad} still incomplete")

    setr_bad = sum(1 for (sh, st) in set_pairs
                   for dst in set_dsts[(sh, st)]
                   if not (H.get((dst, st)) and H[(dst, st)].seller))
    add("set_receivers_sell", SEC_C,
        "Every relocation receiver held the style and sells it",
        setr_bad == 0, f"{len(set_pairs):,} relocations · {setr_bad} to a non-seller")

    # ── D · budget ──
    p1 = sum(x["qty"] for x in rows if x["band"] == "P1")
    add("budget_p1", SEC_D,
        f"Budget — P1 within {pol.movement_budget:,} units, every unit costed exactly 1",
        p1 <= pol.movement_budget + 1e-9,
        f"P1 {p1:,.0f} u of {pol.movement_budget:,} · "
        f"P2 {sum(x['qty'] for x in rows if x['band'] == 'P2'):,.0f} · "
        f"P3 {sum(x['qty'] for x in rows if x['band'] == 'P3'):,.0f}")

    ranked = sorted(R["atoms"].items(),
                    key=lambda kv: (PRIO[kv[0][0]],
                                    -(kv[1]["value"] / kv[1]["units"]), -kv[1]["ros"]))
    expect = band_atoms([a["units"] for _k, a in ranked],
                        pol.movement_budget, pol.budget_p2_extra)
    band_bad = [k for (k, a), eb in zip(ranked, expect, strict=True)
                if R["moves"][a["idxs"][0]]["band"] != eb]
    add("budget_greedy", SEC_D,
        "Banding replays the ONE windowed-greedy law — rank order with "
        "skip-fill; every atom lands exactly where the law puts it",
        not band_bad,
        f"{len(ranked):,} atoms re-banded from scratch · {len(band_bad)} differ")

    grp = defaultdict(set)
    for x in rows:
        if x["program"] in ("HEAL", "SET", "HEAL3", "ASM"):
            grp[(x["program"], x["dst"], x["style"])].add(x["band"])
    atom_bad = sum(1 for v in grp.values() if len(v) > 1)
    add("budget_atomic", SEC_D,
        "Sets fund atomically — a heal, relocation or assembled set is never "
        "split across bands",
        atom_bad == 0, f"{len(grp):,} set-atoms · {atom_bad} split")

    # ── E · capacity, floors & flow balance ──
    normf = R["norm_of"]
    bcat, ocat, icat = Counter(), Counter(), Counter()
    for (sh, _st), h in H.items():
        bcat[(sh, h.cat)] += h.units
    for x in rows:
        ocat[(x["src"], x["cat"])] += x["qty"]
        icat[(x["dst"], x["cat"])] += x["qty"]

    in_chosen, in_w1, in_ch_w1 = Counter(), Counter(), Counter()
    for x in rows:
        if x["program"] not in ("HEAL", "HEAL3"):
            in_chosen[x["dst"]] += x["qty"]
        if x.get("wave") == 1:
            in_w1[x["dst"]] += x["qty"]
            if x["program"] not in ("HEAL", "HEAL3"):
                in_ch_w1[x["dst"]] += x["qty"]

    if not skip_if_off("peak_cap", SEC_E, "Receiver peak cap"):
        peak_bad = []
        heal_only = 0
        for sh in set(recon["inn"]):
            n = normf(sh)
            if n <= 0:
                continue
            bb = recon["before"][sh]
            if in_chosen[sh] <= 0:
                heal_only += 1
            if in_ch_w1[sh] > 0 and bb + in_w1[sh] > max(bb, pol.recv_peak_cap * n) + 1e-6:
                peak_bad.append(("wave1", sh))
            if in_chosen[sh] > 0 and bb + in_chosen[sh] > max(bb, pol.recv_peak_cap * n) + 1e-6:
                peak_bad.append(("chosen", sh))
        add("peak_cap", SEC_E,
            f"RECEIVER PEAK CAP — {pol.recv_peak_cap:.0%} of norm: wave-1 strong form "
            "(chosen saw every wave-1 heal) + chosen inbound alone across the full plan; "
            "heals exempt",
            not peak_bad,
            f"{len(set(recon['inn'])):,} receiving doors · {len(peak_bad)} breaches · "
            f"{heal_only} doors receive set completions only")

    if not skip_if_off("fill_floor", SEC_E, "Fill floor"):
        floor_bad = []
        for sh in recon["end"]:
            n = normf(sh)
            if n <= 0:
                continue
            if recon["end"][sh] < min(recon["before"][sh], pol.fill_floor * n) - 1e-6:
                floor_bad.append(("ALL", sh))
        n_cat = 0
        for (sh, cat), nc in norms.items():
            if nc <= 0:
                continue
            n_cat += 1
            e = bcat[(sh, cat)] - ocat[(sh, cat)] + icat[(sh, cat)]
            if e < min(bcat[(sh, cat)], pol.fill_floor * nc) - 1e-6:
                floor_bad.append((cat, sh))
        n_norm = sum(1 for sh in recon["end"] if normf(sh) > 0)
        add("fill_floor", SEC_E,
            f"FILL FLOOR — no END fill falls below {pol.fill_floor:.0%} of norm: "
            "combined AND per category",
            not floor_bad,
            f"{n_norm} normed doors combined + {n_cat} category norms · "
            f"{len(floor_bad)} pushed below")

    if not skip_if_off("transit_floor", SEC_E, "Transit floor"):
        transit_bad = []
        for sh in set(recon["out"]):
            n = normf(sh)
            if n <= 0:
                continue
            bb = recon["before"][sh]
            if bb - recon["out"][sh] < min(bb, pol.transit_floor * n) - 1e-6:
                transit_bad.append(("ALL", sh))
        for (sh, cat), nc in norms.items():
            if nc <= 0:
                continue
            if bcat[(sh, cat)] - ocat[(sh, cat)] < min(bcat[(sh, cat)],
                                                       pol.transit_floor * nc) - 1e-6:
                transit_bad.append((cat, sh))
        add("transit_floor", SEC_E,
            f"TRANSIT FLOOR — no donor dips below {pol.transit_floor:.0%} of norm DURING "
            "movement: opening − ALL outbound, combined AND per category",
            not transit_bad,
            f"{len(set(recon['out'])):,} donor doors · {len(transit_bad)} dip below")

    if not skip_if_off("fofo_net", SEC_E, "Franchise net flow"):
        fofo_bad = [sh for sh in R["fofo_net"]
                    if recon["inn"][sh] < recon["out"][sh] - 1e-9]
        fp = R["fofo_stat"]
        add("fofo_net", SEC_E,
            "FRANCHISE NET FLOW — every net-ruled door receives AT LEAST as much as it "
            "gives (no-norm franchise doors fully exempt)",
            not fofo_bad,
            f"{fp['net_doors']} net-ruled doors · out {fp['out']:,.0f} u <= in "
            f"{fp['inn']:,.0f} u · {len(fofo_bad)} net-drained · "
            f"{fp['exempt_doors']} exempt ({fp['exempt_out']:,.0f} u shipped — must be 0)")

    if not skip_if_off("fofo_floor", SEC_E, "Franchise transit floor"):
        ffl_bad = []
        for sh in set(recon["out"]):
            if sh not in R["fofo"]:
                continue
            n = normf(sh)
            if n <= 0:
                continue
            bb = recon["before"][sh]
            if bb - recon["out"][sh] < min(bb, pol.fofo_transit_floor * n) - 1e-6:
                ffl_bad.append(("ALL", sh))
        for (sh, cat), nc in norms.items():
            if sh not in R["fofo"] or nc <= 0:
                continue
            if bcat[(sh, cat)] - ocat[(sh, cat)] < min(bcat[(sh, cat)],
                                                       pol.fofo_transit_floor * nc) - 1e-6:
                ffl_bad.append((cat, sh))
        add("fofo_floor", SEC_E,
            f"FRANCHISE FLOOR — no franchise door dips below {pol.fofo_transit_floor:.0%} "
            "of norm during the transfer, combined AND per category",
            not ffl_bad,
            f"{sum(1 for sh in set(recon['out']) if sh in R['fofo'])} franchise donor "
            f"doors · {len(ffl_bad)} dip below")

    if not skip_if_off("style_depth", SEC_E, "Style depth cap"):
        st_in_ch, st_out, st_in = Counter(), Counter(), Counter()
        st_out_w1, st_in_w1, st_in_ch_w1 = Counter(), Counter(), Counter()
        set_srcs = defaultdict(set)
        for x in rows:
            st_out[(x["src"], x["style"])] += x["qty"]
            st_in[(x["dst"], x["style"])] += x["qty"]
            if x["program"] not in ("HEAL", "HEAL3"):
                st_in_ch[(x["dst"], x["style"])] += x["qty"]
            if x.get("wave") == 1:
                st_out_w1[(x["src"], x["style"])] += x["qty"]
                st_in_w1[(x["dst"], x["style"])] += x["qty"]
                if x["program"] not in ("HEAL", "HEAL3"):
                    st_in_ch_w1[(x["dst"], x["style"])] += x["qty"]
            if x["program"] == "SET":
                set_srcs[(x["dst"], x["style"])].add(x["src"])
        depth_bad, worst = [], 0.0
        for (sh, st), q in st_in_ch_w1.items():
            if q <= 0:
                continue
            op = H[(sh, st)].units if (sh, st) in H else 0.0
            end = op - st_out_w1[(sh, st)] + st_in_w1[(sh, st)]
            worst = max(worst, end)
            if end > max(op, pol.style_depth_cap) + 1e-6:
                depth_bad.append(("w1", sh, st))
        for (sh, st), q in st_in_ch.items():
            if q <= 0:
                continue
            op = H[(sh, st)].units if (sh, st) in H else 0.0
            end = op - st_out[(sh, st)] + q
            worst = max(worst, end)
            if end > max(op, pol.style_depth_cap) + 1e-6:
                depth_bad.append(("chosen", sh, st))
        multi = {k: v for k, v in set_srcs.items() if len(v) > 1}
        add("style_depth", SEC_E,
            f"STYLE DEPTH CAP — {pol.style_depth_cap:.0f} u of ONE style: wave-1 strong "
            "form + chosen-only across the full plan; at most one relocated set per "
            "door×style",
            not depth_bad and (not multi if pol.one_set_per_door_style else True),
            f"{len(st_in_ch):,} chosen-receiver style positions · deepest {worst:,.0f} u · "
            f"{len(depth_bad)} over cap · {len(multi)} multi-set stacks")

    # ── F · frozen styles & doors ──
    if not skip_if_off("core_freeze", SEC_F, "Frozen styles never move"):
        core_lines = [x for x in rows if is_core(x["style"])]
        cs = R["core_stat"]
        add("core_freeze", SEC_F,
            "FROZEN STYLES — no movement line names a frozen stylecode (base or variant)",
            not core_lines,
            f"{len(pol.core_styles)} base codes frozen · {cs['holdings']:,} holdings / "
            f"{cs['units']:,.0f} u untouched · {len(core_lines)} lines name one")

    if not skip_if_off("afs_freeze", SEC_F, "Frozen doors touch nothing"):
        afs = pol.afs_stores
        afs_lines = [x for x in rows if x["src"] in afs or x["dst"] in afs]
        afs_moved = sum(
            1 for (sh, st), szm in recon["post"].items() if sh in afs
            and H.get((sh, st)) and any(abs(szm.get(z, 0) - H[(sh, st)].sizes.get(z, 0))
                                        > 1e-9 for z in set(szm) | set(H[(sh, st)].sizes)))
        a = R["afs_stat"]
        add("afs_freeze", SEC_F,
            "FROZEN DOORS (both ways) — named doors neither donate nor receive; stock is "
            "cell-identical before vs after",
            not afs_lines and afs_moved == 0,
            f"{a['doors']} frozen doors / {a['units']:,.0f} u on hand · "
            f"{len(afs_lines)} lines touch one · {afs_moved} positions changed")

    if not skip_if_off("no_norm_one_way", SEC_F, "No-norm doors are one-way"):
        nn = R["nonorm_stat"]
        nn_in_lines = [x for x in rows if x["dst"] in R["no_norm"]]
        add("no_norm_one_way", SEC_F,
            "NO-NORM DOORS ARE ONE-WAY — never receive; useful stock evacuates to "
            "normed doors",
            not nn_in_lines,
            f"{nn['doors']} no-norm doors / {nn['units']:,.0f} u held · "
            f"{nn['out']:,.0f} u evacuated ({nn['reloc']:,.0f} u as whole sets) · "
            f"{len(nn_in_lines)} inbound lines (must be 0)")

    if not skip_if_off("nnf_exempt", SEC_F, "No-norm franchise doors exempt"):
        nnf_lines = [x for x in rows if x["src"] in R["nnf"] or x["dst"] in R["nnf"]]
        nnf_moved = sum(
            1 for (sh, st), szm in recon["post"].items() if sh in R["nnf"]
            and H.get((sh, st)) and any(abs(szm.get(z, 0) - H[(sh, st)].sizes.get(z, 0))
                                        > 1e-9 for z in set(szm) | set(H[(sh, st)].sizes)))
        nf = R["nnf_stat"]
        add("nnf_exempt", SEC_F,
            "NO-NORM FRANCHISE DOORS EXEMPT — neither donate nor receive; stock "
            "cell-identical before vs after",
            not nnf_lines and nnf_moved == 0,
            f"{nf['doors']} doors / {nf['units']:,.0f} u on hand · "
            f"{len(nnf_lines)} lines touch one · {nnf_moved} positions changed")

    if not skip_if_off("new_door_freeze", SEC_F, "New doors never donate"):
        nd_lines = [x for x in rows if x["src"] in R["new_doors"]]
        ndp = R["newdoor_stat"]
        cutoff = (f"{pol.new_door_cutoff:%d-%b-%y}" if pol.new_door_cutoff else "window")
        add("new_door_freeze", SEC_F,
            f"NEW-DOOR DONOR FREEZE — no door opened after {cutoff} ships a single unit",
            not nd_lines,
            f"{ndp['doors']} new doors / {ndp['units']:,.0f} u held · {len(nd_lines)} "
            f"outbound lines (must be 0) · received {ndp['inn']:,.0f} u — receiving open")

    if not skip_if_off("frozen_doors", SEC_F, "Master-frozen doors untouched"):
        fz_lines = [x for x in rows if x["src"] in R["frozen"] or x["dst"] in R["frozen"]]
        fzp = R["frozen_stat"]
        add("frozen_doors", SEC_F,
            "MASTER-FROZEN DOORS — blocked / not-running / frozen-model doors: zero "
            "lines touch them either way",
            not fz_lines,
            f"{fzp['doors']} frozen doors / {fzp['units']:,.0f} u on hand · "
            f"{len(fz_lines)} lines touch one (must be 0)")

    if not skip_if_off("eligible_ends", SEC_F, "Eligible ends"):
        elig_bad = [x for x in rows for e in (x["src"], x["dst"])
                    if stores[e].blocked
                    or (stores[e].status1 and stores[e].status1 != "Running")
                    or e in pol.foco_frozen]
        add("eligible_ends", SEC_F,
            "ELIGIBLE ENDS — both doors of every line are running, unblocked and not "
            "model-frozen in the store master",
            not elig_bad, f"{len(rows):,} lines × 2 ends · {len(elig_bad)} ineligible")

    # ── G · RoS gates ──
    if not skip_if_off("ros_gate", SEC_G, "RoS gate"):
        ros_bad = [x for x in rows if x["program"] in ("SET", "TOPUP", "EXT")
                   and not x["dst_ros"] > x["src_ros"]]
        add("ros_gate", SEC_G,
            "RoS GATE — every chosen destination strictly beats the donor's style RoS",
            not ros_bad,
            f"{sum(1 for x in rows if x['program'] != 'HEAL'):,} chosen-destination "
            f"lines · {len(ros_bad)} downhill")

    if not skip_if_off("ros_confidence", SEC_G, "RoS confidence"):
        conf_bad = []
        for x in rows:
            if x["program"] in ("HEAL", "HEAL3"):
                if x["dst_ros"] < x["src_ros"] - 1e-9:
                    conf_bad.append(x)
            elif x["program"] == "ASM":
                continue
            elif x["dst_ros"] < max(pol.min_recv_ros,
                                    pol.ros_uplift * x["src_ros"]) - 1e-9:
                conf_bad.append(x)
        cu = Counter()
        for x in rows:
            cu[x.get("conf", "")] += x["qty"]
        add("ros_confidence", SEC_G,
            f"RoS CONFIDENCE — chosen destinations clear max({pol.min_recv_ros:.2f}, "
            f"{pol.ros_uplift:.1f}× donor RoS); heals never run RoS-downhill",
            not conf_bad,
            f"{len(rows):,} lines · {len(conf_bad)} miss their bar · confidence: "
            f"HIGH {cu['HIGH']:,.0f} u / OK {cu['OK']:,.0f} u / "
            f"NO-DATA {cu['NO-DATA']:,.0f} u")

    # ── H · waves & assembly ──
    heal_d = {(x["dst"], x["style"]) for x in rows if x["program"] == "HEAL"}
    if not skip_if_off("set_destack", SEC_H, "SET de-stack"):
        set_d = {(x["dst"], x["style"]) for x in rows if x["program"] == "SET"}
        both = heal_d & set_d
        unrec = both - set(R["set_stacked"])
        add("set_destack", SEC_H,
            "SET DE-STACK — a door taking a pool HEAL of a style takes a relocated set "
            "of it only as the recorded no-alternative fallback",
            not unrec,
            f"{len(heal_d):,} healed door×styles · {len(set_d):,} set destinations · "
            f"{len(both)} overlaps, all recorded · {len(unrec)} unrecorded")

    if not skip_if_off("wave2_replay", SEC_H, "Wave 2 replay"):
        w2lbl = "·".join(W2)
        w1_full = ({k for k in heal_dsts}
                   | {(d, st) for (_s, st), ds in set_dsts.items() for d in ds})
        w2_pairs = {(x["dst"], x["style"]) for x in rows if x["program"] == "HEAL3"}
        dbl = w2_pairs & w1_full
        incomplete = [k for k in w2_pairs
                      if any(recon["post"].get(k, {}).get(z, 0) <= 1e-9 for z in W2)]
        post_w1 = {k: dict(H[k].sizes) if k in H else {} for k in
                   {(x["src"], x["style"]) for x in rows if x["program"] == "HEAL3"}}
        for x in rows:
            if x["program"] == "HEAL3":
                continue
            for k in ((x["src"], x["style"]), (x["dst"], x["style"])):
                if k in post_w1:
                    sgn = -1 if k[0] == x["src"] else 1
                    post_w1[k][x["size"]] = post_w1[k].get(x["size"], 0) + sgn * x["qty"]
        broke = [k for k, sz1 in post_w1.items()
                 if all(sz1.get(z, 0) > 1e-9 for z in W2)
                 and any(recon["post"].get(k, {}).get(z, 0) <= 1e-9 for z in W2)]
        w2s = R["wave2_stat"]
        add("wave2_replay", SEC_H,
            f"WAVE 2 ({w2lbl}) — every wave-2 heal lands a complete subset set; no "
            "wave-1 full-set receiver re-served; no donor subset set broken",
            not dbl and not incomplete and not broke,
            f"{len(w2_pairs):,} wave-2 sets / {w2s['units']:,.0f} u · {len(dbl)} "
            f"double-served · {len(incomplete)} incomplete · {len(broke)} donor sets "
            f"broken · availability gap "
            + (f"{w2s['cut3_w1'][0] / w2s['cut3_w1'][1]:.1%} → "
               f"{w2s['cut3_end'][0] / w2s['cut3_end'][1]:.1%}"
               if w2s["cut3_w1"][1] and w2s["cut3_end"][1] else "—"))

    if not skip_if_off("assembly_replay", SEC_H, "Assembly replay"):
        w2lbl = "·".join(W2)
        asm_rows = [x for x in rows if x["program"] == "ASM"]
        asm_pairs = {(x["dst"], x["style"]) for x in asm_rows}
        a_bad = []
        for x in asm_rows:
            if (x["dst"], x["style"]) in H:
                a_bad.append(("not-virgin", x["dst"], x["style"]))
            if not is_dead(H[(x["src"], x["style"])]):
                a_bad.append(("live-donor", x["src"]))
        for k in asm_pairs:
            if any(recon["post"].get(k, {}).get(z, 0) <= 1e-9 for z in W2):
                a_bad.append(("incomplete",) + k)
        per_door = Counter(d for (d, _st) in asm_pairs)
        stacked = [d for d, n2 in per_door.items() if n2 > pol.asm_per_door]
        am = R["asm_stat"]
        add("assembly_replay", SEC_H,
            f"ASSEMBLY — every assembled {w2lbl} set: DEAD donors only, receiver never "
            f"carried the style, sells its department, max {pol.asm_per_door} per door",
            not a_bad and not stacked,
            f"{am['sets']:,} sets / {am['units']:,.0f} u at {am['doors']:,} doors "
            f"({am['styles']:,} styles) · {len(a_bad)} rule breaches · "
            f"{len(stacked)} doors over the cap")

    # ── I · geography & localization ──
    if not skip_if_off("geo_edges", SEC_I, "Transfer geography"):
        geo_ok = make_geo_edge(stores, pol)
        geo_bad = [x for x in rows if not geo_ok(x["src"], x["dst"])]
        gstat = R.get("geo_stat", {})
        add("geo_edges", SEC_I,
            f"TRANSFER GEOGRAPHY — every edge legal under geo_mode={pol.geo_mode!r}, "
            "re-derived from config (allocation, localization and this check share one "
            "predicate)",
            not geo_bad,
            f"{len(rows):,} lines · {len(geo_bad)} out-of-scope · "
            f"{gstat.get('blocked_sets', 0)} heals GEO-BLOCKED at close "
            "(scope cost priced by pan re-run, never by this count)")

    if not skip_if_off("localization", SEC_I, "Donor localization"):
        ls = R["loc_stat"]
        tk = pol.tier_km
        al = dict(pol.city_aliases)
        km_rows = sum(pair_km(stores, x["src"], x["dst"], tk, al)
                      for x in rows if x["program"] != "SET")
        pct = (f"−{(1 - ls['km_after'] / ls['km_before']) * 100:.0f}%"
               if ls["km_before"] else "—")
        add("localization", SEC_I,
            "DONOR LOCALIZATION — every style×size served from the nearest legal donor; "
            "distance re-derived from master coordinates; re-pairing never lengthens "
            "the plan",
            abs(km_rows - ls["km_after"]) < 1.0
            and ls["km_after"] <= ls["km_before"] + 1e-6,
            f"{ls['lines_repaired']:,} of {ls['lines_considered']:,} eligible lines "
            f"re-paired · {ls['km_before']:,.0f} km → {ls['km_after']:,.0f} km ({pct}) · "
            f"rows recompute {km_rows:,.0f} km")

    return b


# (The former `invariant_battery` — pass-rows emitted by construction — was
# deleted after review M2/test-rigor #4: an unfalsifiable battery is not a
# proof surface. The invariant layer's honest projection is `rules.rule_status`
# plus the registry-gated assertions in core.engine._invariants.)
