"""Input sanity — heuristics that interrogate the DATA, not the plan.

The engine's invariants and the build checks prove internal consistency; none
of them can catch a mismapped column, a truncated export or a mis-declared
money column — those load cleanly and produce a confidently wrong plan. This
battery runs between loading and the engine and asks whether the inputs look
like what they claim to be.

Every check here is a scar from production data:
  · money columns arrive EXTENDED (unit x row qty) without warning
  · BI exports silently cap at a round row count and delete whole segments
  · one bulk store order can outweigh a real seasonal signal
  · an exclusion ledger nobody reads is how stores vanish from a network

Severity: info = worth knowing · amber = acknowledge before trusting the run ·
red = blocks the run (override is an explicit CLI/UI act, never a default).
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass

# Known export row caps (BI matrix exports, legacy sheet limits). A raw sheet
# landing EXACTLY on one of these almost certainly lost rows below the cap.
EXPORT_ROW_CAPS = (65536, 100000, 150000, 500000, 1000000, 1048575, 1048576)


@dataclass
class Finding:
    id: str
    level: str            # info | amber | red
    title: str
    detail: str = ""


_RANK = {"info": 0, "amber": 1, "red": 2}


def worst(findings) -> str:
    return max((f.level for f in findings), key=_RANK.get, default="info")


def render(findings) -> str:
    if not findings:
        return "sanity: no findings"
    icon = {"info": "·", "amber": "!", "red": "X"}
    out = []
    for f in sorted(findings, key=lambda f: -_RANK[f.level]):
        out.append(f"[{icon[f.level]}] {f.level.upper():5s} {f.id}: {f.title}")
        for line in filter(None, f.detail.split("\n")):
            out.append(f"      {line}")
    return "\n".join(out)


def run_sanity(inputs, pol, raw_counts=None) -> list:
    """EngineInputs (+ optional raw sheet row counts) -> findings list."""
    F: list[Finding] = []
    holdings, excluded, perf = inputs.holdings, inputs.excluded, inputs.perf

    # ── the emptiest failure of all — nothing loaded (review minor #7) ──
    if not holdings:
        F.append(Finding(
            "no_stock", "red",
            "0 stock rows loaded into the plan universe",
            "the soh profile's category_values / division filters (or the "
            "exclusion rules) matched nothing — a plan over an empty network "
            "would prove itself perfectly and mean nothing"))
        return F

    # ── excluded ledger — nothing vanishes silently ────────────────────
    total_units = sum(h.units if h.units else sum(h.sizes.values())
                      for h in holdings.values())
    excl_units = sum(x["units"] for x in excluded.values())
    universe = total_units + excl_units
    by_reason: dict = {}
    for (reason, _sh), x in excluded.items():
        d = by_reason.setdefault(reason, dict(units=0.0, stores=0))
        d["units"] += x["units"]
        d["stores"] += 1
    if universe > 0:
        share = excl_units / universe
        lines = "\n".join(f"{r}: {d['units']:,.0f} u across {d['stores']} locations"
                          for r, d in sorted(by_reason.items(), key=lambda kv: -kv[1]["units"]))
        level = "red" if share > 0.5 else ("amber" if share > 0.2 else "info")
        F.append(Finding(
            "excluded_ledger", level,
            f"{excl_units:,.0f} of {universe:,.0f} stock units ({share:.1%}) are "
            "excluded from the plan universe",
            lines + ("\n-> more stock excluded than planned: the master/profile "
                     "filters are probably wrong" if level == "red" else "")))

    # ── unit-MRP plausibility — the extended-money detector ───────────
    by_cat: dict = {}
    for h in holdings.values():
        if h.mrp > 0:
            by_cat.setdefault(h.cat, []).append(h.mrp)
    for cat, mrps in sorted(by_cat.items()):
        med = statistics.median(mrps)
        if med > 200_000:
            F.append(Finding(
                "mrp_extended", "red",
                f"{cat}: median unit MRP {med:,.0f} is not a unit price",
                "the money column is almost certainly EXTENDED (unit x row qty) "
                "while the profile declares mrp_extended: false — fix the "
                "declaration, not the data"))
        elif med > 50_000 or med < 10:
            F.append(Finding(
                "mrp_plausibility", "amber",
                f"{cat}: median unit MRP {med:,.0f} is outside the plausible band",
                "check the soh profile's mrp column mapping and its "
                "mrp_extended declaration against a few raw rows"))

    # ── export truncation — raw sheets landing exactly on a known cap ──
    for key, n in (raw_counts or {}).items():
        role, fname, sheet = key
        if isinstance(n, str):
            F.append(Finding("raw_unreadable", "amber",
                             f"{role} {fname}[{sheet}]: {n}"))
        elif n in EXPORT_ROW_CAPS:
            F.append(Finding(
                "export_row_cap", "amber",
                f"{role} {fname}[{sheet}]: exactly {n:,} rows — a known export cap",
                "BI exports truncate silently at round caps and can delete a "
                "whole brand/segment below the cap; re-export with a filter "
                "split and compare totals"))

    # ── bulk outliers — one store's number vs the style's own baseline ──
    style_sales: dict = {}
    for (sh, st), rec in perf.items():
        if rec.get("sales8", 0.0) > 0:
            style_sales.setdefault(st, []).append((sh, rec["sales8"]))
    bulk = []
    for st, rows in style_sales.items():
        if len(rows) < 6:
            continue
        vals = [v for _sh, v in rows]
        med = statistics.median(vals)
        sh, mx = max(rows, key=lambda r: r[1])
        if med > 0 and mx >= 5 * med and mx >= 20:
            bulk.append((mx / med, st, sh, mx, med))
    if bulk:
        bulk.sort(reverse=True)
        lines = "\n".join(
            f"{st} @ store {sh}: {mx:,.0f} u in the window vs style median "
            f"{med:,.0f} ({ratio:.0f}x)" for ratio, st, sh, mx, med in bulk[:8])
        F.append(Finding(
            "bulk_outlier", "amber",
            f"{len(bulk)} store x style sales figures are >=5x their style's own "
            "median — possible bulk/institutional orders",
            lines + "\n-> a single bulk order can fake a velocity signal; check "
                    "GRN history before trusting these as demand"))

    # ── velocity profile — how much of the network has real data ──────
    n_hold = len(holdings)
    if n_hold:
        proxy = sum(1 for k in holdings if k not in perf)
        if proxy:
            share = proxy / n_hold
            F.append(Finding(
                "proxy_share", "amber" if share > 0.4 else "info",
                f"{proxy:,} of {n_hold:,} holdings ({share:.0%}) have no velocity "
                "record — they run as NO-DATA proxies",
                "proxies never donate and never count as dead; a high share "
                "means the velocity export does not cover the stock file"))
    ros_only = sum(1 for r in perf.values()
                   if r.get("sales8", 0) == 0
                   and (r.get("ros8", 0) > 0 or r.get("ros4", 0) > 0))
    if ros_only:
        F.append(Finding(
            "liveness_profile", "info",
            f"{ros_only:,} velocity records carry RoS but zero window sales",
            "liveness reads RoS as well as sales by design — these holdings are "
            "alive, not deadweight"))

    # ── norm coverage over the stocked network ────────────────────────
    stocked = {sh for (sh, _st) in holdings}
    if stocked and inputs.norms:
        cats = {c for (_sh, c) in inputs.norms} | {h.cat for h in holdings.values()}
        no_norm = [sh for sh in stocked
                   if sum(inputs.norms.get((sh, c), 0.0) for c in cats) <= 0]
        if no_norm:
            share = len(no_norm) / len(stocked)
            F.append(Finding(
                "norm_coverage", "amber" if share > 0.3 else "info",
                f"{len(no_norm)} of {len(stocked)} stocked doors ({share:.0%}) "
                "carry no norm",
                "no-norm doors are one-way (evacuate, never receive) when "
                "evac_no_norm is on — a large share suggests the norms file "
                "scope does not match the network"))

    # ── geo coverage — what the distance math actually has ────────────
    if stocked:
        no_geo = sum(1 for sh in stocked
                     if not (sh in inputs.stores and inputs.stores[sh].has_geo))
        if no_geo:
            share = no_geo / len(stocked)
            F.append(Finding(
                "geo_coverage", "amber" if share > 0.3 else "info",
                f"{no_geo} of {len(stocked)} stocked doors ({share:.0%}) have no "
                "coordinates",
                "their distances fall back to city/state/region tier proxies; "
                "localization stays correct but gets coarser"))

    # ── unknown sizes — outside the declared ladder ───────────────────
    ladder = set(getattr(pol, "all_sizes", ()) or ())
    if ladder:
        unknown = Counter()
        for h in holdings.values():
            for z, q in h.sizes.items():
                if z not in ladder:
                    unknown[z] += q
        if unknown:
            share = sum(unknown.values()) / max(total_units, 1e-9)
            lines = "\n".join(f"{z!r}: {q:,.0f} u" for z, q in unknown.most_common(10))
            F.append(Finding(
                "unknown_sizes", "red" if share > 0.2 else "amber",
                f"{sum(unknown.values()):,.0f} units ({share:.0%}) sit in "
                f"{len(unknown)} size value(s) outside the declared ladder",
                lines + "\n-> they load, render and count toward fills, but are "
                        "never pivotal and never EXT-move; a big share here "
                        "means the ladder or size_normalizer is wrong — above "
                        "20% of units this blocks the run"))

    # ── near-miss city spellings — one city masquerading as two ───────
    # Exact-string city equality feeds distance tiers and geo scopes, so
    # "BANGALORE" vs "BENGALURU" or a one-letter typo silently splits a city.
    from ..core.model import canon_city
    aliases = dict(getattr(pol, "city_aliases", ()))
    by_state: dict = {}
    for sh in stocked:
        s = inputs.stores.get(sh)
        if s is None:
            continue
        c = canon_city(s.city, aliases)
        if c:
            by_state.setdefault(canon_city(s.state), set()).add(c)
    near = []
    for state, cities in sorted(by_state.items()):
        cs = sorted(cities)
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                if abs(len(a) - len(b)) <= 2 and _edit_distance(a, b, 2) <= 2:
                    near.append((state or "—", a, b))
    if near:
        lines = "\n".join(f"{st}: {a!r} vs {b!r}" for st, a, b in near[:10])
        F.append(Finding(
            "city_near_miss", "amber",
            f"{len(near)} pair(s) of city spellings within one state differ by "
            "<=2 edits — possibly one city spelled two ways",
            lines + "\n-> if these are the same city, add a city_aliases entry "
                    "to the policy; two spellings read as two cities in "
                    "distance tiers and geo scopes"))

    return F


def _edit_distance(a: str, b: str, cap: int) -> int:
    """Levenshtein with an early-out cap (only exact small distances matter)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = cap + 1
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            best = min(best, cur[j])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]
