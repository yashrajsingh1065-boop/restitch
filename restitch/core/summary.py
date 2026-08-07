"""Canonical plan dump — the comparable, hashable form of a run's result.

The canonical move tuple (program, wave, band, style, size, src, dst, qty),
sorted, IS the plan's identity: golden-master parity, run-to-run verification
and cross-version drift detection all compare exactly this list. Metrics are
derived views; the tuple list is the ground truth.
"""
from __future__ import annotations

from collections import Counter


def canonical_moves(R) -> list:
    # ids are canonical STRINGS (core.ids law); a numeric-string tenant's
    # comparator may int() both sides, an alphanumeric tenant cannot — so
    # the canon itself never does
    return sorted(
        (m["program"], m["wave"], m["band"], m["style"], m["size"],
         str(m["src"]), str(m["dst"]), int(m["qty"]))
        for m in R["moves"])


def metrics(R) -> dict:
    moves = R["moves"]
    prog_units = Counter()
    for m in moves:
        prog_units[m["program"]] += m["qty"]
    f = R["fill"]

    def cutpct(k):
        t = f[k]["tot"].get("ALL", 0)
        return round(f[k]["cut"].get("ALL", 0) / t, 6) if t else 0.0

    excl = Counter()
    for (reason, _sh), x in R["excluded"].items():
        excl[reason] += x["units"]
    ls = R["loc_stat"]
    return dict(
        lines=len(moves),
        units=sum(m["qty"] for m in moves),
        units_by_program={p: int(u) for p, u in sorted(prog_units.items())},
        units_by_band={b: int(u) for b, u in sorted(R["band_units"].items())},
        cut_pct=dict(before=cutpct("before"), p1=cutpct("p1"), full=cutpct("full")),
        localization=dict(km_before=round(ls.get("km_before", 0.0), 3),
                          km_after=round(ls.get("km_after", 0.0), 3),
                          repaired=ls.get("lines_repaired", 0)),
        categories=list(R["categories"]),
        stores_planned=len(R["sb_stores"]),
        excluded_units={r: round(u, 1) for r, u in sorted(excl.items())},
        opening_units=round(R["opening_units"], 1),
        geo=dict(R.get("geo_stat", dict(mode="pan", blocked_sets=0))),
        rules=dict(
            active=sorted(i for i, _t, en in R.get("rule_status", ()) if en),
            skipped=sorted(i for i, _t, en in R.get("rule_status", ()) if not en)),
    )


def _sets_landed(R) -> int:
    return (len(R["completed"]) + len(R["healed_by_set"]) + len(R["relocations"])
            + len(R["completed3"]) + len(R["asm_sets"]))


def scope_cost(R_scoped, R_pan) -> dict:
    """What the transfer geography costs vs the open network — priced by a
    deterministic re-run with geography open. This is the ONLY honest price:
    counting blocked first-gate attempts overstates the unlock, because a
    blocked donor's stock is often consumed by another legal receiver anyway."""
    ms, mp = metrics(R_scoped), metrics(R_pan)
    return dict(
        mode=ms["geo"]["mode"],
        units=ms["units"], units_pan=mp["units"],
        units_cost=mp["units"] - ms["units"],
        sets=_sets_landed(R_scoped), sets_pan=_sets_landed(R_pan),
        sets_cost=_sets_landed(R_pan) - _sets_landed(R_scoped),
        cut_full=ms["cut_pct"]["full"], cut_full_pan=mp["cut_pct"]["full"],
        geo_blocked_sets=ms["geo"]["blocked_sets"],
    )


def plan_json(R) -> dict:
    return dict(moves=[list(t) for t in canonical_moves(R)], metrics=metrics(R))


def text_summary(R) -> str:
    m = metrics(R)
    cp = m["cut_pct"]
    loc = m["localization"]
    lines = [
        f"plan: {m['lines']} lines · {m['units']:.0f} units · "
        f"{m['stores_planned']} doors · categories: {', '.join(m['categories'])}",
        "programs: " + (", ".join(f"{p} {u}" for p, u in m["units_by_program"].items())
                        or "none"),
        "bands:    " + (", ".join(f"{b} {u}" for b, u in m["units_by_band"].items())
                        or "none"),
        f"cut units: {cp['before']:.1%} before -> {cp['p1']:.1%} after P1 "
        f"-> {cp['full']:.1%} after full plan",
        f"shipping:  {loc['km_before']:,.0f} km -> {loc['km_after']:,.0f} km "
        f"({loc['repaired']} donor legs re-paired)",
    ]
    if m["geo"]["mode"] != "pan":
        lines.append(f"geo scope: {m['geo']['mode']} · "
                     f"{m['geo']['blocked_sets']} heal(s) GEO-BLOCKED at close")
    ru = m["rules"]
    if ru["active"] or ru["skipped"]:
        line = f"rules:     {len(ru['active'])} active"
        if ru["skipped"]:
            line += f" · {len(ru['skipped'])} skipped ({', '.join(ru['skipped'])})"
        lines.append(line)
    if m["excluded_units"]:
        lines.append("excluded:  " + ", ".join(f"{r}: {u:,.0f} u"
                                               for r, u in m["excluded_units"].items()))
    return "\n".join(lines)
