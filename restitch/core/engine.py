"""The redeployment kernel — phased fixpoint allocation over one shared ledger.

Operating model (direct store→store, one leg per move):
  1. Stock moves anywhere→anywhere unless a rule forbids the edge.
  2. Warehouse free stock is NOT part of the plan — reported separately.
  3. An article inside a FULL size set never moves individually. Donors are
     cut-set articles only; the one whole-set exception: a DEAD full set may
     relocate INTACT.
  4. Receivers: cut articles land (a) to COMPLETE a cut set, or (b) at doors
     holding the style where its RoS STRICTLY beats the donor's.
  5. Budget: flat — each unit costs 1; atoms fund whole or not at all.

Phase structure (FIXED — correctness, not preference):
  A · HEAL to fixpoint          — all heals before ANY chosen line, so chosen
                                  programs see every heal arrival and the peak-
                                  cap guarantee holds by construction
  B · chosen (SET→TOPUP→EXT)    — rounds to fixpoint (franchise net-flow makes
                                  supply dynamic)
  C · wave-2 subset heals       — on the plan's after-position
  D · assembly                  — new subset sets at dept-selling virgin doors
  E · donor localization        — pure re-pairing, never lengthens the plan

Every mutation goes through move(). Invariants assert before the result is
returned; a plan that cannot prove itself is never rendered.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .ids import id_sort_key
from .localize import donor_localization
from .model import (
    EngineInputs,
    classify,
    dept_map,
    donor_ok,
    eff_ros,
    is_dead,
    make_geo_edge,
    make_season_age,
)
from .policy import HEAL_PROGRAMS, PRIO, Policy, band_atoms
from .rules import RULES, rule_status


def run(inp: EngineInputs, pol: Policy) -> dict:
    stores, tiers = inp.stores, inp.tiers
    holdings, excluded = inp.holdings, inp.excluded
    perf, style_net = inp.perf, inp.style_net
    norms = inp.norms
    fs, fs_meta = inp.fs, inp.fs_meta

    season_age = make_season_age(pol.season_pattern, pol.season_spring_label,
                                 pol.season_current_index)
    classify(holdings, perf, pivotals=pol.pivotals, ros_seller=pol.ros_seller,
             protect_fresh_season=pol.protect_fresh_season, season_age=season_age)
    sdmap, _dept_collisions = dept_map(inp.dept_votes, perf)
    is_core = pol.is_core
    # transfer geography — ONE edge-legality predicate; every edge-forming site
    # below consults it, localization re-checks it, invariant 29 re-derives it
    geo_ok = make_geo_edge(stores, pol)
    PIVOTALS = list(pol.pivotals)
    W2_PIVOTALS = tuple(pol.wave2_pivotals)
    w2_label = "·".join(W2_PIVOTALS)
    DEPTH_FILL = pol.depth_fill
    EXT_SIZES = set(pol.ext_lo_sizes) | set(pol.ext_hi_sizes)

    sb_stores = {shrm for (shrm, _st) in holdings}
    holders_of = defaultdict(set)
    for (shrm, style) in holdings:
        holders_of[style].add(shrm)

    def ros_of(shrm, style):
        """Per store×style effective RoS — the holding's (classify already applied
        the overlays and short-window fallback), else the velocity record for
        sold-out doors, else 0."""
        h = holdings.get((shrm, style))
        if h is not None:
            return h.ros
        rec = perf.get((shrm, style))
        return eff_ros(rec.get("ros8", 0.0), rec.get("ros4", 0.0)) if rec else 0.0

    store_rate = Counter()
    for (shrm, _st), rec in perf.items():
        store_rate[shrm] += rec.get("sales8", 0.0) / 8.0

    # ---- ledger: avail = stock still at the door; arrivals = stock landed by the plan ----
    avail = Counter()
    opening_units = 0.0
    for (shrm, style), h in holdings.items():
        for size, q in h.sizes.items():
            avail[(shrm, style, size)] += q
            opening_units += q

    proj = Counter()                       # store-level projected units
    for (shrm, _st, _sz), q in avail.items():
        proj[shrm] += q
    proj_cat = Counter()               # (store, category) projected units — fill-floor ledger
    for (shrm, _st), h in holdings.items():
        proj_cat[(shrm, h.cat)] += h.units

    # N-category: the category list is derived once from norms ∪ holdings and drives
    # BOTH the floor axes and (downstream) the rendered column sets — one source.
    categories = sorted({c for (_sh, c) in norms} | {h.cat for h in holdings.values()})

    def norm_of(shrm):
        return sum(norms.get((shrm, c), 0.0) for c in categories)

    # door sets: frozen-both-ways / donor-frozen / one-way — all data-driven except
    # the tenant lists carried on the Policy
    afs = pol.afs_stores & sb_stores
    frozen = ({sh for sh in sb_stores
               if stores[sh].blocked
               or (stores[sh].status1 and stores[sh].status1 != "Running")}
              | (pol.foco_frozen & sb_stores)) - afs
    new_doors = ({sh for sh in sb_stores if sh not in frozen and sh not in afs and (
        (stores[sh].opened is not None and stores[sh].opened >= pol.new_door_cutoff)
        or (stores[sh].opened is None and stores[sh].l2l in pol.new_door_l2l))}
        if (pol.new_door_donor_freeze and pol.new_door_cutoff is not None) else set())
    normless = {sh for sh in sb_stores if norm_of(sh) <= 0} - afs
    if pol.evac_no_norm:
        no_norm_all = normless - frozen
    else:
        # containment (review M4): with evacuation off, a normless door has no
        # floor or cap that can bind — recv_room and give_room both read the
        # norm. Leaving it "normal" left it unconstrained both ways, so it
        # freezes in place instead.
        frozen |= normless
        no_norm_all = set()
    fofo = {sh for sh in sb_stores
            if stores[sh].model in pol.franchise_models} - frozen - afs
    # no-norm franchise doors are EXEMPT ENTIRELY — neither donor nor receiver
    nnf = {sh for sh in no_norm_all if sh in fofo}
    no_norm = no_norm_all - nnf
    fofo_net = (fofo - nnf) if pol.fofo_net_flow else set()

    out_tot, in_tot = Counter(), Counter()   # cumulative units OUT / IN per store
    out_cat = Counter()                      # (store, category) cumulative units OUT
    in_chosen_tot = Counter()                # chosen-program inbound per store
    cur_wave = [1]                           # move() stamps each line with its wave

    def recv_room(shrm):
        """Units the door may still RECEIVE before its peak fill — opening +
        everything landed, nothing yet gone — hits the peak cap. A door already
        above the cap gets 0."""
        n = norm_of(shrm)
        if n <= 0:
            return float("inf")     # no-norm doors are blocked as receivers upstream
        op = opening_proj.get(shrm, 0.0)
        return max(op, pol.recv_peak_cap * n) - (op + in_tot[shrm])

    def has_room(shrm, qty):
        return recv_room(shrm) >= qty - 1e-9

    def give_room(shrm, cat):
        """Units of this CATEGORY the store may still donate. Two floors bind at
        once — settled END state ≥ fill_floor of norm, and the DURING-MOVEMENT
        position (opening − out, nothing landed yet) ≥ the transit floor — both
        per category AND combined. Negative for a store already under a floor.
        A net-flow franchise door additionally never ships more than it has
        received SO FAR."""
        tf = pol.fofo_transit_floor if shrm in fofo else pol.transit_floor
        room = float("inf")
        n = norm_of(shrm)
        if n > 0:
            room = min(proj[shrm] - pol.fill_floor * n,
                       opening_proj.get(shrm, 0.0) - out_tot[shrm] - tf * n)
        nc = norms.get((shrm, cat), 0.0)
        if nc > 0:
            room = min(room, proj_cat[(shrm, cat)] - pol.fill_floor * nc,
                       opening_cat.get((shrm, cat), 0.0) - out_cat[(shrm, cat)]
                       - tf * nc)
        if shrm in fofo_net:
            room = min(room, in_tot[shrm] - out_tot[shrm])
        return room

    opening_proj = dict(proj)              # opening store totals — the fill-floor baseline
    opening_cat = dict(proj_cat)           # opening per-category totals
    moves = []                             # THE plan: one dict per direct src→dst line
    arrivals = Counter()                   # (shrm,style,size) -> qty landed
    donated, received = set(), set()       # (shrm,style,size) — round-trip guards
    opening_style = Counter({k: h.units for k, h in holdings.items()})
    style_out, style_in = Counter(), Counter()
    style_in_chosen = Counter()

    def style_room(dst, style):
        """Chosen-program headroom before this door's END holding of ONE style hits
        the style depth cap. A door that OPENED above the cap receives nothing of
        the style. Heals never consult this."""
        op = opening_style[(dst, style)]
        end = op - style_out[(dst, style)] + style_in[(dst, style)]
        return max(op, pol.style_depth_cap) - end

    def move(src, dst, style, size, qty, program, reason):
        assert src != dst, f"self-move {src} {style} {size}"
        assert qty > 0
        avail[(src, style, size)] -= qty
        assert avail[(src, style, size)] > -1e-9, f"over-draw {src} {style} {size}"
        arrivals[(dst, style, size)] += qty
        proj[src] -= qty
        proj[dst] += qty
        out_tot[src] += qty
        in_tot[dst] += qty
        if program not in HEAL_PROGRAMS:
            in_chosen_tot[dst] += qty
            style_in_chosen[(dst, style)] += qty
        style_out[(src, style)] += qty
        style_in[(dst, style)] += qty
        donated.add((src, style, size))
        received.add((dst, style, size))
        h = holdings.get((src, style))
        if h is not None:
            proj_cat[(src, h.cat)] -= qty
            proj_cat[(dst, h.cat)] += qty
            out_cat[(src, h.cat)] += qty
        sros, dros = ros_of(src, style), ros_of(dst, style)
        hd = holdings.get((dst, style))
        conf = ("NO-DATA" if ((hd is not None and hd.proxy) or (sros == 0 and dros == 0))
                else "HIGH" if (dros >= pol.min_recv_ros and dros >= pol.ros_uplift * sros)
                else "OK")
        m = dict(src=src, dst=dst, style=style, size=size, qty=qty, program=program,
                 reason=reason, src_ros=sros, dst_ros=dros, conf=conf,
                 mrp=(h.mrp if h else 0.0), cat=(h.cat if h else ""),
                 dept=sdmap.get(style, ""), wave=cur_wave[0])
        m["value"] = m["mrp"] * qty
        moves.append(m)
        return m

    # ---------- stage 1 · national donor pool (cut-set pivotal articles only) ----------
    # Enrollment is VIRTUAL: nothing is pulled until a receiver claims it. donor_ok
    # encodes the donor law: fresh never · Complete never fragments · Broken always ·
    # Repairable only if dead. Frozen-style / frozen-door / new-door / exempt doors
    # never enroll. No-norm doors enroll EVERY cut holding — selling or not, keep-1
    # front waived — the door is being evacuated, not protected.
    pool = defaultdict(list)               # (style,size) -> [ [shrm, qty, rank] ]
    frag_keep = {}
    frag_enrolled = set()
    for (shrm, style), h in holdings.items():
        if is_core(style) or shrm in afs or shrm in frozen or shrm in new_doors \
                or shrm in nnf:
            continue
        evac = shrm in no_norm
        if not (donor_ok(h) or (evac and h.klass != "Complete" and not h.fresh)):
            continue
        dead = is_dead(h)
        keep = 1 if (pol.protect_selling_fragments and not evac and not h.proxy
                     and h.sales8 > 0) else 0
        if keep:
            frag_keep[(shrm, style)] = keep
        rank = 0 if (dead and h.klass == "Broken") else (1 if dead else 2)
        for size in PIVOTALS:
            q = h.sizes.get(size, 0.0) - keep
            if q > 0:
                pool[(style, size)].append([shrm, q, rank])
                frag_enrolled.add((shrm, style, size))
    for k in pool:
        pool[k].sort(key=lambda e: (e[2], -e[1], id_sort_key(e[0])))  # deadest, big, id order
    pre_supply = Counter({k: sum(e[1] for e in lst) for k, lst in pool.items()})

    def pool_take_set(style, need, dst, rros=0.0, protect3=False, dead_only=False):
        """ALL-OR-NOTHING take across every missing size of one heal, into the
        receiving door `dst`. Skips the receiver itself, any donor outside the
        transfer geography, and any door that already RECEIVED this (style,size)
        (round trip), and respects each donor's floors — a `pending` ledger stops
        one store's headroom being spent twice across two sizes of the same heal.
        Confidence gate: a donor selling the style FASTER than the receiver keeps
        its stock — heals never run RoS-downhill. protect3 (wave 2): a take never
        breaks an EXISTING wave-2 subset set at the donor — breaking one to build
        another is net-zero churn. Nothing mutates unless every size can be fully
        served; returns [(size, entry, qty)] or None."""
        pending = Counter()
        pend3 = Counter()                  # (shrm,size) taken within THIS heal
        got = []
        for sz, n in need.items():
            rem = n
            for e in pool.get((style, sz), ()):
                if rem <= 1e-9:
                    break
                shrm = e[0]
                if shrm == dst or (shrm, style, sz) in received:
                    continue
                if not geo_ok(shrm, dst):
                    continue
                if ros_of(shrm, style) > rros + 1e-9:
                    continue   # no downhill heal
                if dead_only and not is_dead(holdings[(shrm, style)]):
                    continue   # assembly draws dead stock only
                cat = holdings[(shrm, style)].cat
                room = min(e[1], rem, give_room(shrm, cat) - pending[shrm])
                if protect3:
                    cur3 = {z: avail[(shrm, style, z)] + arrivals[(shrm, style, z)]
                            - pend3[(shrm, z)] for z in W2_PIVOTALS}
                    if min(cur3.values()) >= 1 - 1e-9 and sz in cur3:
                        room = min(room, cur3[sz] - 1)   # keep the donor's subset-set whole
                take = float(int(room + 1e-9))
                if take < 1:
                    continue
                got.append((sz, e, take))
                pending[shrm] += take
                pend3[(shrm, sz)] += take
                rem -= take
            if rem > 1e-9:
                return None
        for _sz, e, take in got:
            e[1] -= take
        return got

    def ok_recv(dst, style, sizes):
        """A door enrolled/asked to give up any of these (style,size) keys must not
        now receive that style back — the other half of the round-trip guard."""
        return not any((dst, style, z) in donated or (dst, style, z) in frag_enrolled
                       for z in sizes)

    # selling holders of a style (the ONLY legal chosen destinations), best RoS first.
    sell_holders = defaultdict(list)
    for (shrm, style), h in holdings.items():
        if shrm in afs or shrm in no_norm or shrm in frozen or shrm in nnf:
            continue
        if h.seller and not h.proxy and h.ros >= pol.min_recv_ros:
            sell_holders[style].append((h.ros, shrm))
    for st in sell_holders:
        sell_holders[st].sort(key=lambda x: (-x[0], id_sort_key(x[1])))

    # ---------- allocation ROUNDS ----------
    # The franchise net-flow rule makes supply DYNAMIC: a net-ruled door may only
    # donate what it has already received, so stock locked when a demand was first
    # tried can be free after later moves land inbound there. The stages repeat
    # until a round adds nothing — monotone, bounded by max_rounds.
    healed_by_set = {}                     # (shrm,style) -> src door whose set healed it
    demand = []
    for (shrm, style), h in holdings.items():
        if h.klass != "Repairable" or not h.seller:
            continue
        if is_core(style):
            continue        # frozen-style gaps are refilled by replenishment, not by plan
        if shrm in afs or shrm in no_norm or shrm in frozen or shrm in nnf:
            continue
        demand.append((len(h.missing_piv), h.ros, h.sales8, h.mrp * h.units, shrm, style, h))
    # movements are given to the stores with the HIGHEST RoS, DESCENDING
    # (missing-count is the tiebreak).
    demand.sort(key=lambda x: (-x[1], x[0], -x[2], -x[3], id_sort_key(x[4]), x[5]))

    completed = {}
    relocations, reloc_stay = [], []
    reloc_done = set()                     # (shrm,style) sets already relocated whole
    reloc_recv = set()                     # (dst,style) pairs that took a relocated set
    set_stacked = {}                       # (dst,style) -> src: SET landed on a healed door
                                           # because NO alternative seller cleared the gates
    # Dead Complete sets anywhere + ANY Complete set at a no-norm door — alive or
    # dead — may evacuate to a strictly better normed seller.
    reloc_cands = sorted(
        ((h.mrp * h.units, shrm, style, h) for (shrm, style), h in holdings.items()
         if h.klass == "Complete" and not h.fresh and not is_core(style)
         and shrm not in afs and shrm not in frozen and shrm not in new_doors
         and shrm not in nnf and (is_dead(h) or shrm in no_norm)),
        key=lambda x: (-x[0], id_sort_key(x[1]), x[2]))
    reloc_src_keys = {(sh, st) for _v, sh, st, _h in reloc_cands}
    ext_recv_used = Counter()              # (dst,style) spread cap
    topup_n = 0
    ext_n = 0

    # ── phase A · HEAL rounds to fixpoint ──
    # ALL heals are placed before ANY chosen line — chosen programs then see every
    # heal arrival in in_tot, so the strong cap guarantee ("chosen stock never
    # pushes a door past the peak cap even counting heal arrivals") holds by
    # construction. Rounds matter because heals landing AT a net-flow door unlock
    # that door's own donations for later heals.
    for _rnd in range(pol.max_rounds):
        n_before = len(moves)
        for _nm, _ros, _s8, value, rshrm, style, h in demand:
            if (rshrm, style) in completed or (rshrm, style) in healed_by_set:
                continue
            if not pol.cap_exempt_heals \
                    and not has_room(rshrm, len(h.missing_piv) * DEPTH_FILL):
                continue                   # cap binds heals only if the exemption lever is off
            need = {size: DEPTH_FILL for size in h.missing_piv}
            got = pool_take_set(style, need, rshrm, rros=h.ros)
            if got is None:
                continue       # all-or-nothing: nothing taken; retried next round
            rec = completed.setdefault((rshrm, style),
                                       dict(units_before=h.units, value=value, filled=0,
                                            needed=len(h.missing_piv) * DEPTH_FILL,
                                            cat=h.cat, div=h.div))
            for sz, e, q in got:
                move(e[0], rshrm, style, sz, q, "HEAL",
                     f"completes cut set (from {e[0]})")
                rec["filled"] += q
        if len(moves) == n_before:
            break   # heal fixpoint reached

    # ── phase B · CHOSEN-program rounds (SET → TOPUP → EXT) to fixpoint ──
    # Every placement is cap-checked against in_tot that already carries all heal
    # arrivals. Heal demand is closed — reopening it after chosen lines land could
    # push a chosen receiver past the cap via the heal exemption.
    for _rnd in range(pol.max_rounds):
        n_before = len(moves)

        # ---- stage 3 · full sets relocate WHOLE (the one Complete-set move) ----
        for _val, dshrm, style, h in reloc_cands:
            if (dshrm, style) in reloc_done:
                continue
            need_u = h.units
            if give_room(dshrm, h.cat) < need_u - 1e-9:   # floors: the whole set may not leave
                continue
            all_sizes = [z for z, q in h.sizes.items() if q > 0]
            dros = ros_of(dshrm, style)    # dead ⇒ 0; a live no-norm set sets a real bar
            cands = [(r, s2) for (r, s2) in sell_holders.get(style, ())
                     if s2 != dshrm and (s2, style) not in reloc_src_keys
                     and geo_ok(dshrm, s2)
                     and r >= max(pol.min_recv_ros, pol.ros_uplift * dros) and r > dros
                     and has_room(s2, need_u) and ok_recv(s2, style, all_sizes)
                     and not (pol.one_set_per_door_style and (s2, style) in reloc_recv)
                     and style_room(s2, style) >= need_u - 1e-9]
            # (i) a Repairable-cut seller first — the arriving full set also HEALS it
            heal_c = [(r, s2) for (r, s2) in cands
                      if holdings[(s2, style)].klass == "Repairable"
                      and (s2, style) not in healed_by_set and (s2, style) not in completed]
            dest, dest_kind = None, ""
            if heal_c:
                dest, dest_kind = heal_c[0][1], "heal"
            elif cands:
                # SET DE-STACK: a door already healed from the pool keeps just its
                # completion — the set goes to the NEXT seller down the RoS list.
                # Stacking is the recorded fallback when no other seller is legal.
                fresh_c = ([c2 for c2 in cands if (c2[1], style) not in completed]
                           if pol.set_avoid_heal_dest else cands)
                if fresh_c:
                    dest, dest_kind = fresh_c[0][1], "ros"
                else:
                    dest, dest_kind = cands[0][1], "ros"
                    set_stacked[(dest, style)] = dshrm
            if dest is None:
                continue      # no RoS-uphill home yet; retried next round
            why = (f"whole set evacuated from no-norm door {dshrm}"
                   if dshrm in no_norm and not is_dead(h) else f"whole dead set from {dshrm}")
            for size in all_sizes:
                move(dshrm, dest, style, size, h.sizes[size], "SET", why)
            if dest_kind == "heal":
                healed_by_set[(dest, style)] = dshrm
            reloc_done.add((dshrm, style))
            reloc_recv.add((dest, style))
            relocations.append(dict(style=style, src=dshrm, dst=dest, units=h.units,
                                    value=h.mrp * h.units, dest_kind=dest_kind))

        # ---- stage 4 · TOPUP — pivotal REPLENISHMENT DEPTH into full-set doors ----
        # A door holding even 1 unit of EVERY pivotal is a full-set door; the plan
        # sends it ADDITIONAL pivotal units toward the depth target, highest-RoS
        # doors first; once the best door holds the target, leftover distributes
        # down the RoS list. Each pivotal tops up independently. Donors clear
        # every gate.
        full_now = {k for k, h in holdings.items() if h.klass == "Complete"}
        full_now |= set(completed) | set(healed_by_set)
        topup_recv = defaultdict(list)     # style -> [(ros, shrm)] full-set sellers
        for (shrm, style) in full_now:
            h = holdings[(shrm, style)]
            if shrm in afs or shrm in no_norm or shrm in frozen or shrm in nnf:
                continue
            if h.seller and not h.proxy and h.ros >= pol.min_recv_ros:
                topup_recv[style].append((h.ros, shrm))
        for st in topup_recv:
            topup_recv[st].sort(key=lambda x: (-x[0], id_sort_key(x[1])))

        for depth in range(2, pol.topup_depth_target + 1):
            for style in sorted(topup_recv):
                for r, dst in topup_recv[style]:
                    if (dst, style) in reloc_src_keys:
                        continue
                    for size in PIVOTALS:
                        have = avail[(dst, style, size)] + arrivals[(dst, style, size)]
                        need = depth - have         # end-state depth vs this round's target
                        if need < 1:
                            continue
                        for e in pool.get((style, size), ()):
                            if need < 1:
                                break
                            src, q = e[0], e[1]
                            if q < 1 or src == dst:
                                continue
                            if (src, style, size) in received or not geo_ok(src, dst):
                                continue
                            sros = ros_of(src, style)
                            if r <= sros or r < max(pol.min_recv_ros,
                                                    pol.ros_uplift * sros):
                                continue   # strict climb, matching SET/EXT
                                           # (a tie once crashed invariant 7
                                           # under the permissive preset)
                            if not ok_recv(dst, style, [size]):
                                continue
                            room = min(q, need,
                                       give_room(src, holdings[(src, style)].cat),
                                       recv_room(dst),          # peak cap
                                       style_room(dst, style))  # style depth cap
                            take = float(int(max(0.0, room)))
                            if take < 1:
                                continue
                            move(src, dst, style, size, take, "TOPUP",
                                 f"replenish pivotal {size} depth {have:.0f}→"
                                 f"{have + take:.0f}: RoS {sros:.2f}→{r:.2f}")
                            e[1] -= take
                            need -= take
                            topup_n += 1

        # ---- stage 5 · EXT — extreme sizes out of DEAD CUT holdings, RoS-gated ----
        # An extreme size attached to a Complete holding is frozen WITH its set.
        # Only dead cut holdings shed extremes — plus LIVE cut holdings at no-norm
        # doors (evacuation) — and only uphill in RoS with the confidence margin.
        # Extremes are NAMED ladder members; a size in neither set (unknown sizes
        # included) loads and renders but never EXT-moves.
        for (shrm, style), h in sorted(holdings.items(),
                                       key=lambda kv: (id_sort_key(kv[0][0]), kv[0][1])):
            if is_core(style) or h.klass == "Complete" or h.fresh:
                continue
            if shrm in afs or shrm in frozen or shrm in new_doors or shrm in nnf:
                continue
            if not (is_dead(h) or shrm in no_norm):
                continue
            sros = ros_of(shrm, style)     # dead ⇒ 0; live no-norm stock sets a real bar
            for size in sorted(h.sizes):
                if size not in EXT_SIZES:
                    continue
                q = avail[(shrm, style, size)]
                if q < 1:
                    continue
                for r, dst in sell_holders.get(style, ()):
                    if q < 1:
                        break
                    if dst == shrm or r < max(pol.min_recv_ros, pol.ros_uplift * sros) \
                            or r <= sros:
                        continue
                    if (dst, style) in reloc_src_keys or not ok_recv(dst, style, [size]):
                        continue
                    if not geo_ok(shrm, dst):
                        continue
                    cap = pol.lo_spread - ext_recv_used[(dst, style)]
                    if cap < 1:
                        continue
                    room = min(cap, give_room(shrm, h.cat),
                               recv_room(dst),
                               style_room(dst, style))
                    take = float(int(min(q, max(0.0, room))))
                    if take < 1:
                        continue
                    move(shrm, dst, style, size, take, "EXT",
                         f"extreme {size} to seller: RoS {sros:.2f}→{r:.2f}")
                    ext_recv_used[(dst, style)] += take
                    q -= take
                    ext_n += 1

        if len(moves) == n_before:
            break   # a full pass added nothing — fixpoint reached

    # ── phase C · WAVE 2 — subset sets on the AFTER-SOH ──
    # Take the plan's after-position as the new opening and CREATE MORE SIZE SETS
    # under the wave-2 subset lens at selling doors. Same donor pool (same donor
    # law by construction), same floors/caps/ledgers continuing, same RoS-
    # descending order, same no-downhill gate, all-or-nothing per set. A door
    # that already received a full set of a style in wave 1 NEVER receives that
    # style again. protect3 keeps existing subset sets whole at donors.
    arr_sizes = defaultdict(set)           # (sh,st) -> sizes with plan arrivals

    def _cut3():
        """(units in holdings missing any wave-2 pivotal, total units) on the
        CURRENT state."""
        for (sh, st, z), q in arrivals.items():
            if q > 1e-9:
                arr_sizes[(sh, st)].add(z)
        cut = tot = 0.0
        for (sh, st), h in holdings.items():
            zs = set(h.sizes) | arr_sizes[(sh, st)] | set(W2_PIVOTALS)
            u = sum(avail[(sh, st, z)] + arrivals[(sh, st, z)] for z in zs)
            if u <= 1e-9:
                continue
            tot += u
            if any(avail[(sh, st, z)] + arrivals[(sh, st, z)] <= 1e-9 for z in W2_PIVOTALS):
                cut += u
        return cut, tot

    cut3_open = (sum(h.units for h in holdings.values()
                     if any(h.sizes.get(z, 0) <= 0 for z in W2_PIVOTALS)),
                 sum(h.units for h in holdings.values()))
    cut3_w1 = _cut3()                      # subset cut position AFTER wave 1, BEFORE wave 2
    complete3_pre = {(sh, st) for (sh, st) in holdings
                     if all(avail[(sh, st, z)] + arrivals[(sh, st, z)] > 1e-9
                            for z in W2_PIVOTALS)}
    wave1_full_recv = set(completed) | set(healed_by_set) | set(reloc_recv)
    cur_wave[0] = 2
    demand3 = []
    for (shrm, style), h in holdings.items():
        if is_core(style):
            continue
        if shrm in afs or shrm in no_norm or shrm in frozen or shrm in nnf:
            continue
        if (shrm, style) in wave1_full_recv:
            continue      # never the same style twice
        if not h.seller or h.proxy:
            continue
        cur = {z: avail[(shrm, style, z)] + arrivals[(shrm, style, z)] for z in W2_PIVOTALS}
        missing = [z for z, q in cur.items() if q <= 1e-9]
        if not missing or len(missing) == len(W2_PIVOTALS):
            continue   # complete-subset / no base
        demand3.append((h.ros, len(missing), h.sales8, h.mrp * h.units, shrm, style))
    demand3.sort(key=lambda x: (-x[0], x[1], -x[2], -x[3], id_sort_key(x[4]), x[5]))

    completed3 = {}
    w2_cap_blocked = set()
    if W2_PIVOTALS:
        for _rnd in range(pol.max_rounds):
            n_before = len(moves)
            for ros3, _nm, _s8, _val, rshrm, style in demand3:
                if (rshrm, style) in completed3:
                    continue
                need = {z: DEPTH_FILL for z in W2_PIVOTALS
                        if avail[(rshrm, style, z)] + arrivals[(rshrm, style, z)] <= 1e-9}
                if not need:
                    continue
                if not ok_recv(rshrm, style, list(need)):
                    continue       # round-trip guard
                qty_need = sum(need.values())
                if not pol.cap_exempt_wave2:
                    if in_chosen_tot[rshrm] > 0 and not has_room(rshrm, qty_need):
                        w2_cap_blocked.add((rshrm, style))
                        continue
                    if (style_in_chosen[(rshrm, style)] > 0
                            and style_room(rshrm, style) < qty_need - 1e-9):
                        w2_cap_blocked.add((rshrm, style))
                        continue
                got = pool_take_set(style, need, rshrm, rros=ros3, protect3=True)
                if got is None:
                    continue
                w2_cap_blocked.discard((rshrm, style))
                rec = completed3.setdefault((rshrm, style), dict(filled=0, ros=ros3))
                for sz, e, q in got:
                    move(e[0], rshrm, style, sz, q, "HEAL3",
                         f"wave 2 — completes {w2_label} set (from {e[0]})")
                    rec["filled"] += q
            if len(moves) == n_before:
                break

    # ── phase D · ASSEMBLY — new subset sets at dept-selling virgin doors ──
    # Narrow by construction: dead donors only (moving dead stock to a live
    # department is uphill by definition — the style-RoS ladder does not exist at
    # a virgin door), receivers ranked by department sales descending, capped per
    # door, and EVERY chosen-program gate binds in full.
    asm_sets = {}
    dept_sales = Counter()                     # (store, dept) -> recent sales
    if pol.assembly_enabled and W2_PIVOTALS:
        for (sh2, st2), rec2 in perf.items():
            d2 = sdmap.get(st2, "")
            if d2:
                dept_sales[(sh2, d2)] += max(0.0, rec2.get("sales8", 0.0))
        recv_by_dept = defaultdict(list)
        for (sh2, d2), sal in dept_sales.items():
            if sal <= 0 or sh2 not in sb_stores:
                continue
            if sh2 in afs or sh2 in frozen or sh2 in no_norm or sh2 in nnf:
                continue
            recv_by_dept[d2].append((sal, sh2))
        for d2 in recv_by_dept:
            recv_by_dept[d2].sort(key=lambda x: (-x[0], id_sort_key(x[1])))
        held_style = defaultdict(set)
        for (sh2, st2) in holdings:
            held_style[st2].add(sh2)
        asm_used = Counter()                   # door -> assembled sets landed
        asm_styles = sorted({st for (st, z) in pool if z in W2_PIVOTALS and not is_core(st)})
        for style in asm_styles:
            dept = sdmap.get(style, "")
            if not dept:
                continue
            for _sal, dst in recv_by_dept.get(dept, ()):
                if asm_used[dst] >= pol.asm_per_door:
                    continue
                if dst in held_style[style]:
                    continue          # virgin doors only — else heal
                need = {z: DEPTH_FILL for z in W2_PIVOTALS}
                qn = sum(need.values())
                if not has_room(dst, qn) or style_room(dst, style) < qn - 1e-9:
                    continue
                got = pool_take_set(style, need, dst, rros=0.0,
                                    protect3=True, dead_only=True)
                if got is None:
                    break          # style's dead supply exhausted → next style
                for sz, e, q in got:
                    mm = move(e[0], dst, style, sz, q, "ASM",
                              f"assembled {w2_label} set at {dept} seller")
                    mm["conf"] = "DEPT"        # picked on department velocity, not style RoS
                asm_used[dst] += 1
                asm_sets[(dst, style)] = dict(dept=dept)
    cur_wave[0] = 1
    cut3_end = _cut3()

    # ---------- post-rounds bookkeeping · gaps and stayed relocations ----------
    gap_structural, gap_geo, gap_priority, gap_capacity = [], [], [], []
    national_gap = Counter()
    for _nm, _ros, _s8, _value, rshrm, style, h in demand:
        if (rshrm, style) in completed or (rshrm, style) in healed_by_set:
            continue
        need = {size: DEPTH_FILL for size in h.missing_piv}
        if not pol.cap_exempt_heals and not has_room(rshrm, sum(need.values())):
            gap_capacity.append((rshrm, style, list(need)))
            continue
        struct = [sz for sz in need if pre_supply[(style, sz)] < DEPTH_FILL]
        if struct:
            gap_structural.append((rshrm, style, struct))
            for sz in struct:
                national_gap[(style, sz)] += need[sz]
            continue
        # GEO-BLOCKED: donor supply for a needed size still exists at close, but
        # none of it inside this receiver's transfer geography. An honest label,
        # NOT the price of the scope — blocked first-gate counts overstate the
        # unlock; the true cost is stated by the deterministic re-run with
        # geography open (see summary.scope_cost).
        geo_sz = [sz for sz in need
                  if any(e[1] >= 1 for e in pool.get((style, sz), ()))
                  and not any(e[1] >= 1 and geo_ok(e[0], rshrm)
                              for e in pool.get((style, sz), ()))]
        if geo_sz:
            gap_geo.append((rshrm, style, geo_sz))
        else:                              # supply exists on paper but is consumed,
            gap_priority.append((rshrm, style, list(need)))   # floored, or gate-blocked
    for _val, dshrm, style, h in reloc_cands:
        if (dshrm, style) not in reloc_done:
            reloc_stay.append(dict(shrm=dshrm, style=style, units=h.units,
                                   value=h.mrp * h.units))

    # ── phase E · DONOR LOCALIZATION ──
    loc_stat = donor_localization(moves, stores, holdings, ros_of, pol)

    # ---------- disposition of every cut holding (report-only) ----------
    liq_styles = {st for st, net in style_net.items() if net.get("sales8", 0) <= 0}
    gs = {(a, b2) for a, b2, _s in gap_structural}
    gg = {(a, b2) for a, b2, _s in gap_geo}
    gp = {(a, b2) for a, b2, _s in gap_priority}
    gc = {(a, b2) for a, b2, _s in gap_capacity}
    disposition = Counter()
    disp_units = Counter()
    for (shrm, style), h in holdings.items():
        if not h.cut:
            continue
        if is_core(style):
            b = "CORE — frozen by buyer; auto-replenishment refills"
        elif shrm in afs:
            b = "AFS door — frozen, neither donates nor receives"
        elif shrm in frozen:
            b = "blocked / not-open / FOCO door — frozen (buyer 24-Jul)"
        elif shrm in nnf:
            b = "no-norm FOFO — exempt, stock stays (buyer 24-Jul)"
        elif (shrm, style) in completed:
            b = "healed"
        elif (shrm, style) in healed_by_set:
            b = "healed by whole-set relocation"
        elif shrm in no_norm:
            b = "no-norm door — evacuation feedstock for good stores"
        elif (shrm, style) in gc:
            b = "blocked — door over the 140% receiver peak cap"
        elif (shrm, style) in gs:
            b = "structural gap — size absent nationally (FRESH BUY)"
        elif (shrm, style) in gg:
            b = "GEO-BLOCKED — donors exist, none inside the transfer geography"
        elif (shrm, style) in gp:
            b = "lost to priority — supply consumed / floored / FOFO-capped"
        elif shrm in new_doors:
            b = "new door (opened <1yr) — donor-frozen, stock stays"
        elif h.klass == "Broken" and style in liq_styles:
            b = "liquidate (style dead network-wide)"
        else:
            b = "residual fragment — stays at door"
        disposition[b] += 1
        disp_units[b] += h.units

    # ---------- report-only stat blocks ----------
    core_all = [h for (sh, st), h in holdings.items() if is_core(st)]
    core_stat = dict(styles=len({st for (sh, st) in holdings if is_core(st)}),
                     holdings=len(core_all), units=sum(h.units for h in core_all),
                     cut_holdings=sum(1 for h in core_all if h.cut),
                     cut_units=sum(h.units for h in core_all if h.cut))
    afs_stat = dict(doors=len(afs),
                    units=sum(h.units for (sh, _st), h in holdings.items() if sh in afs))
    nn_out = sum(m["qty"] for m in moves if m["src"] in no_norm)
    nonorm_stat = dict(doors=len(no_norm),
                       units=sum(h.units for (sh, _st), h in holdings.items()
                                 if sh in no_norm),
                       out=nn_out,
                       reloc=sum(r["units"] for r in relocations if r["src"] in no_norm))
    conf_units = Counter()
    for m in moves:
        conf_units[m["conf"]] += m["qty"]
    frozen_stat = dict(doors=len(frozen),
                       units=sum(h.units for (sh, _st), h in holdings.items()
                                 if sh in frozen))
    newdoor_stat = dict(doors=len(new_doors),
                        units=sum(h.units for (sh, _st), h in holdings.items()
                                  if sh in new_doors),
                        inn=sum(m["qty"] for m in moves if m["dst"] in new_doors))
    fofo_stat = dict(doors=len(fofo), net_doors=len(fofo_net),
                     exempt_doors=len(nnf),
                     units=sum(h.units for (sh, _st), h in holdings.items() if sh in fofo),
                     out=sum(m["qty"] for m in moves if m["src"] in fofo),
                     inn=sum(m["qty"] for m in moves if m["dst"] in fofo),
                     exempt_out=sum(m["qty"] for m in moves if m["src"] in nnf))
    nnf_stat = dict(doors=len(nnf),
                    units=sum(h.units for (sh, _st), h in holdings.items() if sh in nnf))
    wave2_stat = dict(demand=len(demand3), sets=len(completed3),
                      units=sum(m["qty"] for m in moves if m["program"] == "HEAL3"),
                      lines=sum(1 for m in moves if m["program"] == "HEAL3"),
                      cap_blocked=len(w2_cap_blocked),
                      cut3_open=cut3_open, cut3_w1=cut3_w1, cut3_end=cut3_end)
    asm_stat = dict(sets=len(asm_sets),
                    units=sum(m["qty"] for m in moves if m["program"] == "ASM"),
                    doors=len({sh for (sh, _st) in asm_sets}),
                    styles=len({st for (_sh, st) in asm_sets}))

    # ---------- budget · flat, atomic per set, ₹ healed per unit ----------
    atoms = defaultdict(lambda: dict(units=0.0, value=0.0, ros=0.0, idxs=[]))
    for i, m in enumerate(moves):
        if m["program"] in ("HEAL", "SET", "HEAL3", "ASM"):
            key = (m["program"], m["dst"], m["style"])         # a set funds whole or not at all
        else:
            key = (m["program"], i)                            # TOPUP/EXT are per-line atoms
        a = atoms[key]
        a["units"] += m["qty"]
        a["value"] += m["value"]
        a["ros"] = max(a["ros"], m["dst_ros"])
        a["idxs"].append(i)
    ranked = sorted(atoms.items(),
                    key=lambda kv: (PRIO[kv[0][0]],
                                    -(kv[1]["value"] / kv[1]["units"]),
                                    -kv[1]["ros"]))
    bands = {}                              # move idx -> band
    band_units = Counter()
    for (_key, a), band in zip(ranked, band_atoms([a["units"] for _k, a in ranked],
                                                 pol.movement_budget,
                                                 pol.budget_p2_extra),
                              strict=True):
        band_units[band] += a["units"]
        for i in a["idxs"]:
            bands[i] = band
    for i, m in enumerate(moves):
        m["band"] = bands[i]

    # ---------- fill / cut measurement (before · P1 · full) ----------
    def measure(sel):
        sizes = {k: dict(h.sizes) for k, h in holdings.items()}
        for m in moves:
            if not sel(m):
                continue
            d = sizes.setdefault((m["src"], m["style"]), {})
            d[m["size"]] = d.get(m["size"], 0) - m["qty"]
            d = sizes.setdefault((m["dst"], m["style"]), {})
            d[m["size"]] = d.get(m["size"], 0) + m["qty"]
        cut, tot, store_soh = Counter(), Counter(), Counter()
        for (shrm, _style), sz in sizes.items():
            u = sum(q for q in sz.values() if q > 0)
            if u <= 0:
                continue
            store_soh[shrm] += u
            broken = any(sz.get(p, 0) <= 0 for p in PIVOTALS)
            for key in (tiers.get(shrm, "—"), "ALL"):
                tot[key] += u
                if broken:
                    cut[key] += u
        normsum = Counter()
        sohsum = Counter()
        for shrm, u in store_soh.items():
            for key in (tiers.get(shrm, "—"), "ALL"):
                sohsum[key] += u
                normsum[key] += norm_of(shrm)
        return dict(cut=cut, tot=tot, store_soh=store_soh, norm=normsum, soh=sohsum)

    fill = dict(before=measure(lambda m: False),
                p1=measure(lambda m: m["band"] == "P1"),
                full=measure(lambda m: True))

    # ---------- FS gap report (separate — NOT part of the plan) ----------
    fs_report = []
    open_gaps = Counter()
    for _sh, st, szs in gap_structural:
        for z in szs:
            open_gaps[(st, z)] += DEPTH_FILL
    for _sh, st, szs in gap_priority:
        for z in szs:
            open_gaps[(st, z)] += DEPTH_FILL
    for (style, size), need in sorted(open_gaps.items(), key=lambda kv: -kv[1]):
        w = fs.get((style, size), {})
        have = sum(w.values())
        if have > 0:
            meta = fs_meta.get(style, {})
            fs_report.append(dict(style=style, size=size, need=need, fs_total=have,
                                  wh={c: w.get(c, 0.0) for c in
                                      (pol.fs_warehouses or sorted(w))},
                                  mrp=meta.get("mrp", 0.0), cat=meta.get("cat", "")))

    R = dict(stores=stores, holdings=holdings, tiers=tiers, norms=norms, perf=perf,
             style_net=style_net, sdmap=sdmap, sb_stores=sb_stores, store_rate=store_rate,
             moves=moves, completed=completed, healed_by_set=healed_by_set,
             relocations=relocations, reloc_stay=reloc_stay,
             set_stacked=set_stacked, loc_stat=loc_stat,
             gap_structural=gap_structural, gap_geo=gap_geo,
             gap_priority=gap_priority,
             geo_stat=dict(mode=pol.geo_mode, blocked_sets=len(gap_geo)),
             gap_capacity=gap_capacity, afs=afs, no_norm=no_norm,
             frozen=frozen, new_doors=new_doors, fofo=fofo, fofo_net=fofo_net,
             nnf=nnf, nnf_stat=nnf_stat,
             frozen_stat=frozen_stat, newdoor_stat=newdoor_stat, fofo_stat=fofo_stat,
             completed3=completed3, wave1_full_recv=wave1_full_recv,
             complete3_pre=complete3_pre, wave2_stat=wave2_stat,
             asm_sets=asm_sets, asm_stat=asm_stat, dept_sales=dept_sales, sdmap_ref=sdmap,
             norms_aw26=inp.norms_context,
             afs_stat=afs_stat, nonorm_stat=nonorm_stat, conf_units=conf_units,
             national_gap=national_gap, disposition=disposition, disp_units=disp_units,
             frag_keep=frag_keep, frag_enrolled=frag_enrolled, pre_supply=pre_supply,
             avail=avail, arrivals=arrivals, opening_units=opening_units,
             bands=bands, band_units=band_units, atoms=dict(atoms), fill=fill,
             fs=fs, fs_meta=fs_meta, fs_report=fs_report, norm_of=norm_of,
             ros_of=ros_of, excluded=excluded, liq_styles=liq_styles,
             opening_proj=opening_proj, proj=proj,
             opening_cat=opening_cat, proj_cat=proj_cat, core_stat=core_stat,
             categories=categories, policy=pol)
    _invariants(R, pol)
    R["rule_status"] = rule_status(pol)
    return R


# ─────────────────────────────── invariants ───────────────────────────────
def _invariants(R, pol: Policy):
    holdings, moves, stores = R["holdings"], R["moves"], R["stores"]
    avail, arrivals = R["avail"], R["arrivals"]
    PIVOTALS = list(pol.pivotals)
    W2_PIVOTALS = tuple(pol.wave2_pivotals)
    is_core = pol.is_core
    # rule-owned invariants execute only while their rule is in force — the
    # registry's "toggling a rule toggles its whole proof chain" is a fact
    # here, not a caption (review M2: a disabled rule's invariant once crashed
    # the documented permissive costing path)
    on = {r.id: bool(r.enabled(pol)) for r in RULES}

    # 1 · conservation — moves shuffle units, never create or destroy them
    final_total = sum(avail.values()) + sum(arrivals.values())
    assert abs(final_total - R["opening_units"]) < 1e-6, "conservation broken"
    # 2 · no over-draw
    assert all(q > -1e-9 for q in avail.values()), "negative availability"
    # 3 · full-set freeze — a Complete holding's units move ONLY as a whole SET relocation
    set_src = {(m["src"], m["style"]) for m in moves if m["program"] == "SET"}
    for m in moves:
        h = holdings.get((m["src"], m["style"]))
        if h is not None and h.klass == "Complete":
            assert m["program"] == "SET", f"article moved out of a full set {m}"
    for (sh, st) in set_src:
        h = holdings[(sh, st)]
        moved = sum(m["qty"] for m in moves
                    if m["program"] == "SET" and m["src"] == sh and m["style"] == st)
        assert abs(moved - h.units) < 1e-9, f"partial full-set move {sh} {st}"
        dsts = {m["dst"] for m in moves
                if m["program"] == "SET" and m["src"] == sh and m["style"] == st}
        assert len(dsts) == 1, f"full set split across doors {sh} {st}"
    # 4 · cut-only fragment donors (a cut holding at a no-norm door is always a donor)
    for m in moves:
        if m["program"] in ("HEAL", "HEAL3", "ASM", "TOPUP"):
            h = holdings[(m["src"], m["style"])]
            assert donor_ok(h) or (m["src"] in R["no_norm"]
                                   and h.klass != "Complete" and not h.fresh), \
                f"non-donor fragment source {m}"
    # 5 · keep-fronts survive (selling Broken keeps 1 of each held pivotal)
    viol = 0
    for (shrm, style), keep in R["frag_keep"].items():
        h = holdings[(shrm, style)]
        for sz in PIVOTALS:
            if h.sizes.get(sz, 0) >= keep and avail[(shrm, style, sz)] < keep - 1e-9:
                viol += 1
    assert viol == 0, f"{viol} selling-fragment keeps violated"
    # 6 · receiver legality — stocked door; SET/TOPUP/EXT receivers held the style at
    # open; ASM receivers are VIRGIN for the style by definition
    holders_by_style = defaultdict(set)
    for (sh, st) in holdings:
        holders_by_style[st].add(sh)
    for m in moves:
        assert m["dst"] in R["sb_stores"], f"receiver without category stock {m}"
        s = stores[m["dst"]]
        assert not s.vw and not s.junk, f"excluded receiver {m}"
        if m["program"] in ("SET", "TOPUP", "EXT"):
            assert m["dst"] in holders_by_style[m["style"]], \
                f"chosen receiver never held the style {m}"
        elif m["program"] == "ASM":
            assert m["dst"] not in holders_by_style[m["style"]], \
                f"ASM receiver already held the style {m}"
    # 7 · RoS gate — every CHOSEN destination strictly climbs the style-RoS gradient
    for m in moves:
        if m["program"] in ("SET", "TOPUP", "EXT"):
            assert m["dst_ros"] > m["src_ros"], f"RoS gate violated {m}"
    # 8 · fresh (current-season) never a source
    if on["fresh"]:
        for m in moves:
            h = holdings.get((m["src"], m["style"]))
            assert not (h and h.fresh), f"fresh stock moved {m}"
    # 9 · no self-moves, no round trips
    ships = {(m["src"], m["style"], m["size"]) for m in moves}
    recvs = {(m["dst"], m["style"], m["size"]) for m in moves}
    assert not any(m["src"] == m["dst"] for m in moves), "self move"
    rt = ships & recvs
    assert not rt, f"round trips {sorted(rt)[:3]}"
    # 10 · heal completion — every completed door ends holding all pivotals
    for (shrm, style) in list(R["completed"]) + list(R["healed_by_set"]):
        for p in PIVOTALS:
            end = avail[(shrm, style, p)] + arrivals[(shrm, style, p)]
            assert end > 1e-9, f"heal didn't complete {shrm} {style} {p}"
    # 11 · structural-gap honesty — the size truly had no national donor supply
    for _sh, style, szs in R["gap_structural"]:
        for z in szs:
            assert R["pre_supply"][(style, z)] < pol.depth_fill, f"bogus gap {style} {z}"
    # 12 · budget — flat 1u=1, P1 within budget, atoms atomic
    p1 = sum(m["qty"] for m in moves if m["band"] == "P1")
    assert p1 <= pol.movement_budget + 1e-9, f"P1 over budget {p1}"
    for key, a in R["atoms"].items():
        bset = {moves[i]["band"] for i in a["idxs"]}
        assert len(bset) == 1, f"atom split across bands {key}"
    # 13 · warehouse stock untouched — every end of every move is a KNOWN
    # store (membership, not typing: ids are str, and a warehouse code that
    # merely looks like a store number must still refuse)
    S13 = R["stores"]
    assert all(m["src"] in S13 and m["dst"] in S13 for m in moves), \
        "non-store id in movement plan"
    # 14 · fill floor — COMBINED and PER CATEGORY; a store that opened below a floor
    # is never made worse (it cannot donate)
    normf = R["norm_of"]
    if on["floors"]:
        for sh, end in R["proj"].items():
            n = normf(sh)
            if n <= 0:
                continue
            floor = min(R["opening_proj"].get(sh, 0.0), pol.fill_floor * n)
            assert end >= floor - 1e-6, \
                f"combined fill floor broken at {sh}: end {end:,.0f} < floor {floor:,.0f}"
        for (sh, cat), end in R["proj_cat"].items():
            nc = R["norms"].get((sh, cat), 0.0)
            if nc <= 0:
                continue
            floor = min(R["opening_cat"].get((sh, cat), 0.0), pol.fill_floor * nc)
            assert end >= floor - 1e-6, \
                f"{cat} fill floor broken at {sh}: end {end:,.0f} < floor {floor:,.0f}"
    # 15 · frozen-style freeze — never move, base or variant form
    if on["style_freeze"]:
        core_moved = [m for m in moves if is_core(m["style"])]
        assert not core_moved, f"frozen style in movement plan: {core_moved[:3]}"
    # 16 · frozen-door (AFS) freeze — neither donate nor receive
    if on["door_freeze_afs"]:
        afs_touch = [m for m in moves if m["src"] in R["afs"] or m["dst"] in R["afs"]]
        assert not afs_touch, f"frozen (AFS) door in movement plan: {afs_touch[:3]}"
    # 17 · no-norm doors are ONE-WAY — they only ever donate, never receive
    if on["no_norm"]:
        nn_in = [m for m in moves if m["dst"] in R["no_norm"]]
        assert not nn_in, f"inbound to a no-norm door: {nn_in[:3]}"
    # 18 · flow balance — transit floor per cat + combined; receiver peak cap in two
    # forms: (a) wave-1 keeps the strong guarantee (any door taking wave-1 chosen
    # inbound keeps its wave-1 peak, heal arrivals included, within the cap);
    # (b) across the FULL plan, chosen inbound alone never pushes a door past it.
    outs, ins, ins_ch, outsc = Counter(), Counter(), Counter(), Counter()
    ins_w1, ins_ch_w1 = Counter(), Counter()
    for m in moves:
        outs[m["src"]] += m["qty"]
        ins[m["dst"]] += m["qty"]
        if m["program"] not in HEAL_PROGRAMS:
            ins_ch[m["dst"]] += m["qty"]
        if m["wave"] == 1:
            ins_w1[m["dst"]] += m["qty"]
            if m["program"] not in HEAL_PROGRAMS:
                ins_ch_w1[m["dst"]] += m["qty"]
        h = holdings.get((m["src"], m["style"]))
        if h is not None:
            outsc[(m["src"], h.cat)] += m["qty"]

    def _tf(sh):
        return pol.fofo_transit_floor if sh in R["fofo"] else pol.transit_floor

    if on["floors"]:
        for sh, o in outs.items():
            n = normf(sh)
            if n <= 0:
                continue
            op = R["opening_proj"].get(sh, 0.0)
            assert op - o >= min(op, _tf(sh) * n) - 1e-6, \
                (f"transit floor broken at {sh}: {op:,.0f} − {o:,.0f} out "
                 f"< {_tf(sh):.0%} of {n:,.0f}")
        for (sh, cat), o in outsc.items():
            nc = R["norms"].get((sh, cat), 0.0)
            if nc <= 0:
                continue
            op = R["opening_cat"].get((sh, cat), 0.0)
            assert op - o >= min(op, _tf(sh) * nc) - 1e-6, \
                f"{cat} transit floor broken at {sh}"
    if on["peak_cap"]:
        for sh, iq in ins_w1.items():
            n = normf(sh)
            if n <= 0 or ins_ch_w1[sh] <= 0:
                continue  # heal-only doors are exempt by rule
            op = R["opening_proj"].get(sh, 0.0)
            assert op + iq <= max(op, pol.recv_peak_cap * n) + 1e-6, \
                f"wave-1 peak cap broken at {sh}: {op:,.0f} + {iq:,.0f} in"
        for sh, iq in ins_ch.items():
            n = normf(sh)
            if n <= 0 or iq <= 0:
                continue
            op = R["opening_proj"].get(sh, 0.0)
            assert op + iq <= max(op, pol.recv_peak_cap * n) + 1e-6, \
                f"chosen peak cap broken at {sh}: {op:,.0f} + {iq:,.0f} chosen in"
    # 19 · RoS confidence — heals (both waves) never downhill: KERNEL law, the
    # pool take enforces it in every configuration. The uplift bar on chosen
    # dests is the ros_gates rule's surface.
    for m in moves:
        if m["program"] in HEAL_PROGRAMS:
            assert m["dst_ros"] >= m["src_ros"] - 1e-9, f"downhill heal {m}"
        elif m["program"] != "ASM" and on["ros_gates"]:
            assert m["dst_ros"] >= max(pol.min_recv_ros,
                                       pol.ros_uplift * m["src_ros"]) - 1e-9, \
                f"confidence bar missed {m}"
    # 20 · new-door donor freeze — a recently opened door never appears as a source
    if on["new_door"]:
        nd_out = [m for m in moves if m["src"] in R["new_doors"]]
        assert not nd_out, f"new door donated stock: {nd_out[:3]}"
    # 21 · frozen doors — zero lines touch them either way
    fz = [m for m in moves if m["src"] in R["frozen"] or m["dst"] in R["frozen"]]
    assert not fz, f"frozen door in movement plan: {fz[:3]}"
    # 22 · franchise net flow — every net-ruled door receives at least as much as it gives
    if on["fofo_net_flow"]:
        for sh in R["fofo_net"]:
            assert ins[sh] >= outs[sh] - 1e-9, \
                f"franchise door {sh} net-drained: out {outs[sh]:,.0f} > in {ins[sh]:,.0f}"
    # 23 · style depth cap — wave-1 strong form + chosen-only full plan; one
    # relocated set per door×style
    st_in_ch, st_out, st_in = Counter(), Counter(), Counter()
    st_out_w1, st_in_w1, st_in_ch_w1 = Counter(), Counter(), Counter()
    set_srcs = defaultdict(set)
    for m in moves:
        st_out[(m["src"], m["style"])] += m["qty"]
        st_in[(m["dst"], m["style"])] += m["qty"]
        if m["program"] not in HEAL_PROGRAMS:
            st_in_ch[(m["dst"], m["style"])] += m["qty"]
        if m["wave"] == 1:
            st_out_w1[(m["src"], m["style"])] += m["qty"]
            st_in_w1[(m["dst"], m["style"])] += m["qty"]
            if m["program"] not in HEAL_PROGRAMS:
                st_in_ch_w1[(m["dst"], m["style"])] += m["qty"]
        if m["program"] == "SET":
            set_srcs[(m["dst"], m["style"])].add(m["src"])
    if on["style_depth"]:
        for (sh, st), q in st_in_ch_w1.items():
            if q <= 0:
                continue
            op = holdings[(sh, st)].units if (sh, st) in holdings else 0.0
            end = op - st_out_w1[(sh, st)] + st_in_w1[(sh, st)]
            assert end <= max(op, pol.style_depth_cap) + 1e-6, \
                f"wave-1 style depth cap broken at {sh} {st}: end {end:,.0f} u"
        for (sh, st), q in st_in_ch.items():
            if q <= 0:
                continue
            op = holdings[(sh, st)].units if (sh, st) in holdings else 0.0
            end = op - st_out[(sh, st)] + q
            assert end <= max(op, pol.style_depth_cap) + 1e-6, \
                f"chosen style depth cap broken at {sh} {st}: {end:,.0f} u"
    if pol.one_set_per_door_style:
        multi = {k: v for k, v in set_srcs.items() if len(v) > 1}
        assert not multi, f"multiple relocated sets stacked: {list(multi)[:3]}"
    # 24 · WAVE 2 — every wave-2 completed door ends holding the whole subset; no
    # wave-2 receiver took a style it already received a full set of in wave 1;
    # no door that held a complete subset after wave 1 lost it (protect3)
    avail_, arr_ = R["avail"], R["arrivals"]
    if on["wave2"]:
        for (sh, st) in R["completed3"]:
            assert (sh, st) not in R["wave1_full_recv"], f"wave-2 double-serve {sh} {st}"
            for z in W2_PIVOTALS:
                assert avail_[(sh, st, z)] + arr_[(sh, st, z)] > 1e-9, \
                    f"wave-2 heal incomplete {sh} {st} {z}"
        for m in moves:
            if m["program"] == "HEAL3":
                assert m["dst"] not in R["frozen"] and m["dst"] not in R["afs"] \
                    and m["dst"] not in R["no_norm"], f"wave-2 heal to excluded door {m}"
        for (sh, st) in R["complete3_pre"]:
            for z in W2_PIVOTALS:
                assert avail_[(sh, st, z)] + arr_[(sh, st, z)] > 1e-9, \
                    f"wave 2 broke an existing subset set at {sh} {st} ({z})"
    # 25 · ASSEMBLY — every assembled set: lands COMPLETE, at a door that never
    # carried the style, that SELLS the style's department, capped per door,
    # from DEAD donors only
    if on["assembly"]:
        asm_by_door = Counter()
        for m in moves:
            if m["program"] != "ASM":
                continue
            h = holdings[(m["src"], m["style"])]
            assert is_dead(h), f"ASM from a live donor {m}"
            dept = R["sdmap_ref"].get(m["style"], "")
            assert R["dept_sales"].get((m["dst"], dept), 0.0) > 0, f"ASM to non-dept-seller {m}"
        for (sh, st) in R["asm_sets"]:
            asm_by_door[sh] += 1
            for z in W2_PIVOTALS:
                assert arr_[(sh, st, z)] > 1e-9, f"assembled set incomplete {sh} {st} {z}"
        assert all(v <= pol.asm_per_door for v in asm_by_door.values()), \
            "ASM per-door cap broken"
    # 26 · exempt doors (no-norm franchise) — zero lines touch them either way
    if on["no_norm"]:
        nnf_touch = [m for m in moves if m["src"] in R["nnf"] or m["dst"] in R["nnf"]]
        assert not nnf_touch, f"exempt door touched: {nnf_touch[:3]}"
    # 27 · SET DE-STACK — a door healed from the pool takes a relocated set of that
    # style ONLY as the recorded no-alternative fallback
    if on["destack"]:
        heal_d = {(m["dst"], m["style"]) for m in moves if m["program"] == "HEAL"}
        set_d = {(m["dst"], m["style"]) for m in moves if m["program"] == "SET"}
        stacked = heal_d & set_d
        assert stacked <= set(R["set_stacked"]), \
            f"unrecorded HEAL+SET stack: {sorted(stacked - set(R['set_stacked']))[:3]}"
    # 28 · DONOR LOCALIZATION — a pure re-pairing that never lengthens the plan; the
    # per-store shipment/receipt equality is asserted at the moment of re-pairing
    if on["localize"]:
        ls = R["loc_stat"]
        assert ls["km_after"] <= ls["km_before"] + 1e-6, "localization lengthened the plan"
    # 29 · TRANSFER GEOGRAPHY — every move's edge is legal under the configured
    # scope. The predicate is RE-DERIVED here from config, so allocation,
    # localization and this assertion cannot drift apart silently.
    if on["geo_scope"]:
        geo_ok = make_geo_edge(stores, pol)
        geo_bad = [m for m in moves if not geo_ok(m["src"], m["dst"])]
        assert not geo_bad, \
            f"out-of-scope transfer under geo_mode={pol.geo_mode!r}: {geo_bad[:3]}"
