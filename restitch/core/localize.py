"""Donor localization — Hungarian re-pairing of donors to receivers.

"If a style×size can be received from a nearby store, prioritise the nearby
transfer." Within every (program-class, style, size, qty) group the donors are
re-matched to the receivers to minimise total shipping distance. Pure
re-pairing: every store still ships and receives the exact same units
(asserted); only WHO ships to WHOM changes. Legality is re-checked
edge-by-edge with the same RoS gates the stages used, so the identity pairing
is always feasible and the optimum can only shorten the plan. SET lines never
re-pair — a relocated whole set travels from ONE donor door by construction.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .model import canon_city, make_geo_edge, pair_km
from .policy import HEAL_PROGRAMS, Policy

# HEAL and TOPUP draw from the same fragment pool, so their donors are
# interchangeable within a (style, size, qty) group; EXT, wave-2 and assembly
# each re-pair only among themselves.
_CLASS = {"HEAL": "W1POOL", "TOPUP": "W1POOL", "EXT": "W1EXT",
          "HEAL3": "W2", "ASM": "ASM"}


def edge_legal(prog, sros, dros, src, dst, pol: Policy, geo=None) -> bool:
    """The re-pairing legality predicate — same gates the allocation stages used,
    transfer geography included: a re-pairing that crossed the geo scope would
    undo in phase E what allocation enforced in phases A-D."""
    if src == dst:
        return False
    if geo is not None and not geo(src, dst):
        return False
    if prog in HEAL_PROGRAMS:               # never downhill
        return dros >= sros - 1e-9
    if prog in ("TOPUP", "EXT"):            # strict climb + uplift bar
        return dros > sros and dros >= max(pol.min_recv_ros, pol.ros_uplift * sros) - 1e-9
    return True                             # ASM: dead donors, dept-picked receiver


def donor_localization(moves, stores, holdings, ros_of, pol: Policy) -> dict:
    """Re-pair in place; returns the localization stat block."""
    loc_stat = dict(groups=0, lines_considered=0, lines_repaired=0,
                    km_before=0.0, km_after=0.0,
                    same_city_before=0, same_city_after=0, geo_pairs=0)
    if not pol.localize_donors:
        return loc_stat

    import numpy as _np
    from scipy.optimize import linear_sum_assignment as _lsa

    tier_km = pol.tier_km
    aliases = dict(pol.city_aliases)
    geo_ok = make_geo_edge(stores, pol)
    _city = {sh: canon_city(s.city, aliases) for sh, s in stores.items()}

    def _pair_km(a, b):
        return pair_km(stores, a, b, tier_km, aliases)

    pre_out, pre_in = Counter(), Counter()
    for m in moves:
        pre_out[(m["src"], m["style"], m["size"])] += m["qty"]
        pre_in[(m["dst"], m["style"], m["size"])] += m["qty"]
    loc_groups = defaultdict(list)
    for i, m in enumerate(moves):
        cl = _CLASS.get(m["program"])
        if cl is not None:
            loc_groups[(cl, m["style"], m["size"], m["qty"])].append(i)

    for _gk, idxs in sorted(loc_groups.items()):
        n = len(idxs)
        loc_stat["groups"] += 1
        loc_stat["lines_considered"] += n
        d0 = [_pair_km(moves[i]["src"], moves[i]["dst"]) for i in idxs]
        loc_stat["km_before"] += sum(d0)
        loc_stat["same_city_before"] += sum(
            1 for i in idxs if _city[moves[i]["src"]] == _city[moves[i]["dst"]])
        if n == 1:
            loc_stat["km_after"] += d0[0]
            loc_stat["same_city_after"] += sum(
                1 for i in idxs if _city[moves[i]["src"]] == _city[moves[i]["dst"]])
            continue
        srcs = [moves[i]["src"] for i in idxs]
        C = _np.empty((n, n))
        for a in range(n):
            sros_by_style = ros_of(srcs[a], moves[idxs[0]]["style"])
            for b in range(n):
                mb = moves[idxs[b]]
                if edge_legal(mb["program"], sros_by_style, mb["dst_ros"],
                              srcs[a], mb["dst"], pol, geo=geo_ok):
                    C[a, b] = _pair_km(srcs[a], mb["dst"])
                else:
                    C[a, b] = 1e9
        rI, cI = _lsa(C)
        new_cost = float(C[rI, cI].sum())
        base_cost = sum(d0)
        if new_cost >= base_cost - 1e-6:        # no strict improvement: keep the pairing
            loc_stat["km_after"] += base_cost
            loc_stat["same_city_after"] += sum(
                1 for i in idxs if _city[moves[i]["src"]] == _city[moves[i]["dst"]])
            continue
        assert new_cost < 1e8, "assignment picked an illegal edge"
        loc_stat["km_after"] += new_cost
        for a in range(n):
            j = idxs[cI[a]]                     # receiver line now served by donor srcs[a]
            m2 = moves[j]
            ns = srcs[a]
            if m2["src"] != ns:
                old = m2["src"]
                okm, nkm = _pair_km(m2["src"], m2["dst"]), _pair_km(ns, m2["dst"])
                h2 = holdings[(ns, m2["style"])]
                m2["src"] = ns
                m2["src_ros"] = ros_of(ns, m2["style"])
                m2["mrp"] = h2.mrp
                m2["value"] = h2.mrp * m2["qty"]
                if m2["program"] != "ASM":      # ASM keeps its DEPT confidence tag
                    hd2 = holdings.get((m2["dst"], m2["style"]))
                    m2["conf"] = ("NO-DATA" if ((hd2 is not None and hd2.proxy)
                                                or (m2["src_ros"] == 0 and m2["dst_ros"] == 0))
                                  else "HIGH" if (m2["dst_ros"] >= pol.min_recv_ros
                                                  and m2["dst_ros"]
                                                  >= pol.ros_uplift * m2["src_ros"])
                                  else "OK")
                m2["reason"] += f" · donor localized {old}→{ns} ({okm:,.0f}→{nkm:,.0f} km)"
                loc_stat["lines_repaired"] += 1
            loc_stat["same_city_after"] += 1 if _city[m2["src"]] == _city[m2["dst"]] else 0

    post_out, post_in = Counter(), Counter()
    for m in moves:
        post_out[(m["src"], m["style"], m["size"])] += m["qty"]
        post_in[(m["dst"], m["style"], m["size"])] += m["qty"]
    assert post_out == pre_out and post_in == pre_in, \
        "localization changed a store's shipments or receipts"
    assert loc_stat["km_after"] <= loc_stat["km_before"] + 1e-6
    return loc_stat
