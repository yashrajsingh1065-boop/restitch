"""Synthetic demo network — raw-file-shaped, deliberately messy, tenant-free.

`write_demo(dir)` writes what a real tenant hands the product: a store master
with a junk banner row and blocking flags, a stock file with EXTENDED money
columns and rows that must be excluded, a velocity export with a planted bulk
outlier, norms with decoy brand rows, a tier CSV — plus the mapping profiles,
policy and manifest that make them load. `restitch demo` runs this through the
FULL pipeline: manifest -> loaders -> sanity -> engine.

Everything is deterministic per seed. Nothing here is real: names, codes and
geography are invented; the messes are re-creations of production scars.
"""
from __future__ import annotations

import csv
import datetime
import random
from pathlib import Path

import yaml

from .core.policy import Policy, to_dict

# ── vocabularies — one synthetic network per category shape ─────────────
# tailoring: numeric drop ladder, zero-padded. footwear-uk: half-size shoe
# ladder written as FLOATS in the raw file (6.5, 7.0) so the uk_shoe
# normalizer is genuinely exercised, not just configured.
VOCABS = {
    "tailoring": dict(
        sizes=("084", "088", "092", "096", "100", "104", "108"),
        pivotals=("088", "092", "096"), secondary=("084", "100"),
        wave2=("092", "096"), ext_lo=("084",), ext_hi=("100", "104", "108"),
        size_order=("084", "088", "092", "096", "100", "104"),
        cats={"Jacket": "JACKET", "Trouser": "TROUSER"},
        dept={"Jacket": "TAILORED", "Trouser": "BOTTOMS"},
        styles=[f"JKT{i:03d}" for i in range(1, 11)]
        + [f"TRS{i:03d}" for i in range(1, 7)],
        cat_of=lambda st: "Jacket" if st.startswith("JKT") else "Trouser",
        mrp={"Jacket": (4999, 6999, 8999, 12999), "Trouser": (1999, 2499, 2999)},
        normalizer="numeric_zfill3",
        raw_size=lambda z: z,
    ),
    "footwear-uk": dict(
        sizes=("6", "6.5", "7", "7.5", "8", "8.5", "9", "10"),
        pivotals=("7", "8", "9"), secondary=("6.5", "7.5", "8.5"),
        wave2=("8", "9"), ext_lo=("6",), ext_hi=("10",),
        size_order=("6", "6.5", "7", "7.5", "8", "8.5", "9", "10"),
        cats={"Derby": "DERBY", "Loafer": "LOAFER"},
        dept={"Derby": "FORMAL SHOES", "Loafer": "CASUAL SHOES"},
        styles=[f"DRB{i:03d}" for i in range(1, 11)]
        + [f"LFR{i:03d}" for i in range(1, 7)],
        cat_of=lambda st: "Derby" if st.startswith("DRB") else "Loafer",
        mrp={"Derby": (5999, 7999, 9999), "Loafer": (3999, 4999, 6999)},
        normalizer="uk_shoe",
        raw_size=lambda z: float(z),
        id_prefix="FW-",       # alphanumeric store codes — the str-id proof
    ),
}
SOH_ASOF = datetime.date(2026, 7, 15)

_REGIONS = {"NORTH": (28.0, 77.0), "SOUTH": (12.5, 78.0),
            "EAST": (22.5, 88.0), "WEST": (19.0, 73.5)}
_STATES = {"NORTH": ("ARGENT", "BASALT"), "SOUTH": ("CINNABAR", "DOLOMITE"),
           "EAST": ("EMERY", "FELDSPAR"), "WEST": ("GARNET", "HEMATITE")}
_TOWNS = ("FALLS", "JUNCTION", "HEIGHTS", "CROSSING")


def demo_policy(vocab: str = "tailoring") -> Policy:
    """The demo preset: dates unpinned (the manifest derives them from
    soh_asof), tenant lists empty, budget sized to the synthetic network."""
    v = VOCABS[vocab]
    return Policy(
        all_sizes=v["sizes"], size_order=v["size_order"],
        pivotals=v["pivotals"], secondary=v["secondary"],
        wave2_pivotals=v["wave2"],
        ext_lo_sizes=v["ext_lo"], ext_hi_sizes=v["ext_hi"],
        movement_budget=1500, budget_p2_extra=300,
    )


def make_network(seed: int = 8, vocab: str = "tailoring") -> dict:
    """All demo data as plain row dicts — the single source both file writers
    and tests draw from."""
    v = VOCABS[vocab]
    CATS, DEPT, STYLES = v["cats"], v["dept"], v["styles"]
    PIVOTALS, SECONDARY = v["pivotals"], v["secondary"]
    rng = random.Random(seed)
    stores, cities = [], []
    for region, (alat, alon) in _REGIONS.items():
        for state in _STATES[region]:
            for town in rng.sample(_TOWNS, 2):
                cities.append(dict(city=f"{state} {town}", state=state, region=region,
                                   lat=alat + rng.uniform(-1.5, 1.5),
                                   lon=alon + rng.uniform(-1.5, 1.5)))
    pfx = v.get("id_prefix", "")
    sid = 9001
    for c in cities:
        for _n in range(rng.choice((3, 4))):
            opened = datetime.datetime(rng.randint(2018, 2024), rng.randint(1, 12), 15)
            stores.append(dict(
                id=f"{pfx}{sid}" if pfx else sid,
                name=f"DEMO MART {c['city']} {sid}", **c,
                model=("FOFO" if rng.random() < 0.25 else "COCO"),
                opened=opened, l2l="L2L", status="Running", block="",
                grade=rng.choice("ABC"), stype="MALL" if rng.random() < 0.4 else "STREET",
                fmt="CORE"))
            sid += 1
    # the deliberate messes: a blocked door, a project-phase door, three doors
    # opened inside the new-door window, one excluded-channel door, two doors
    # with no coordinates
    stores[3]["block"] = "RENOVATION"
    stores[7]["status"] = "Project Phase"
    for i, (y, m) in zip((11, 19, 27), ((2025, 9), (2026, 2), (2026, 5)),
                     strict=True):
        stores[i]["opened"] = datetime.datetime(y, m, 10)
    outlet = dict(stores[5], id=f"{pfx}{sid}" if pfx else sid,
                  name=f"DEMO LIQUIDATION OUTLET {sid}")
    stores.append(outlet)
    sid += 1
    for i in (14, 22):
        stores[i]["lat"] = stores[i]["lon"] = None

    real_ids = [s["id"] for s in stores]
    style_cat = {st: v["cat_of"](st) for st in STYLES}
    style_mrp = {st: rng.choice(v["mrp"][style_cat[st]]) for st in STYLES}

    soh, holding_keys = [], []
    for s in stores:
        for st in STYLES:
            if rng.random() > 0.55:
                continue
            cat = style_cat[st]
            fresh = rng.random() < 0.15
            season = ("Autumn Winter 2026" if fresh
                      else rng.choice(("Spring Summer 2026", "Autumn Winter 2025",
                                       "Spring Summer 2025")))
            shape = rng.random()
            if shape < 0.35:          # complete set
                sizes = {p: rng.randint(1, 2) for p in PIVOTALS}
            elif shape < 0.80:        # cut set: 1-2 pivotals missing
                keep = rng.sample(PIVOTALS, rng.choice((1, 2)))
                sizes = {p: rng.randint(1, 2) for p in keep}
            else:                     # extremes / secondary only
                fringe = tuple(dict.fromkeys(SECONDARY + v["ext_lo"] + v["ext_hi"]))
                sizes = {z: rng.randint(1, 3) for z in rng.sample(fringe, 2)}
            if rng.random() < 0.3:
                sizes[rng.choice(SECONDARY)] = sizes.get(rng.choice(SECONDARY), 0) + 1
            for z, q in sizes.items():
                if q <= 0:
                    continue
                soh.append(dict(div="MW", store=s["id"], cat=CATS[cat], size=z,
                                style=st, qty=q, mrp_ext=style_mrp[st] * q,
                                value=round(style_mrp[st] * q * 0.55, 2),
                                season=season, dept=DEPT[cat]))
            holding_keys.append((s["id"], st))
    # trap rows: a store missing from the master + a filtered-out division
    ghost = 9999
    for st in rng.sample(STYLES, 4):
        soh.append(dict(div="MW", store=ghost, cat=CATS[style_cat[st]], size="092",
                        style=st, qty=2, mrp_ext=style_mrp[st] * 2,
                        value=style_mrp[st], season="Spring Summer 2025",
                        dept=DEPT[style_cat[st]]))
    for st in rng.sample(STYLES, 6):
        soh.append(dict(div="WW", store=rng.choice(real_ids), cat=CATS[style_cat[st]],
                        size="096", style=st, qty=1, mrp_ext=style_mrp[st],
                        value=style_mrp[st] * 0.5, season="Spring Summer 2025",
                        dept=DEPT[style_cat[st]]))

    ros = []
    for (shrm, st) in holding_keys:
        if rng.random() > 0.75:
            continue                  # ~25% run as NO-DATA proxies
        sold = rng.choice((0, 0, 0, 0, 0, 1, 1, 2, 3, 4, 6))
        r8 = round(sold / 8.0 * rng.uniform(0.7, 1.3), 3) if sold else 0.0
        ros.append(dict(store=shrm, style=st, ros8=r8, sales8=sold,
                        ros_life=round(r8 * rng.uniform(0.8, 1.2), 3),
                        strr=round(rng.uniform(0.2, 0.9), 2),
                        sales_life=sold * rng.randint(2, 6),
                        dept=DEPT[style_cat[st]], shelf=rng.randint(30, 400),
                        nsv_life=sold * style_mrp[st] * 3, nsv8=sold * style_mrp[st],
                        inwards=sold + rng.randint(0, 5), grn8=rng.randint(0, 4)))
    # sold-out doors: velocity without stock (they can still receive)
    for st in rng.sample(STYLES, 5):
        sh = rng.choice(real_ids)
        if (sh, st) not in holding_keys:
            ros.append(dict(store=sh, style=st, ros8=round(rng.uniform(0.2, 0.5), 3),
                            sales8=rng.randint(2, 5), ros_life=0.3, strr=0.7,
                            sales_life=12, dept=DEPT[style_cat[st]], shelf=120,
                            nsv_life=30000, nsv8=9000, inwards=14, grn8=2))
    # the planted bulk outlier: one door "sells" 40 units of one style in the window
    bulk_store, bulk_style = real_ids[8], STYLES[2]
    ros = [r for r in ros if not (r["store"] == bulk_store and r["style"] == bulk_style)]
    ros.append(dict(store=bulk_store, style=bulk_style, ros8=5.0, sales8=40,
                    ros_life=4.0, strr=0.95, sales_life=60,
                    dept=DEPT[style_cat[bulk_style]],
                    shelf=60, nsv_life=500000, nsv8=350000, inwards=42, grn8=40))

    norms, normless = [], set(rng.sample(real_ids, 3))
    craws = list(CATS.values())
    for s in stores:
        if s["id"] in normless:
            continue
        norms.append(dict(store=s["id"], brand="DEMO", cat=craws[0],
                          qty=rng.randint(18, 60)))
        norms.append(dict(store=s["id"], brand="DEMO", cat=craws[1],
                          qty=rng.randint(10, 40)))
        if rng.random() < 0.3:        # decoy rows the brand_scope filter must drop
            norms.append(dict(store=s["id"], brand="OTHER", cat=craws[0], qty=999))

    tiers = [dict(store=s["id"], tier=f"T{rng.randint(1, 5)}")
             for s in stores if rng.random() < 0.85]
    return dict(stores=stores, soh=soh, ros=ros, norms=norms, tiers=tiers)


# ── raw-file writers ────────────────────────────────────────────────────
def _wb(rows, header, sheet, path, banner=None):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if banner:
        ws.append([banner])
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _profiles(v):
    cat_values = {c: [raw] for c, raw in v["cats"].items()}
    return {
        "master.yaml": _MASTER_PROFILE,
        "soh.yaml": dict(
            role="soh", name="demo-soh", sheet="SOH", header_row=0,
            columns={"division": "Division", "location": "Store Code",
                     "category": "Category", "size": "Size", "style": "Style Code",
                     "qty": "Qty", "mrp": "MRP", "value": "Value",
                     "season": "Season", "dept": "Department"},
            semantics=dict(mrp_extended=True, category_values=cat_values,
                           size_normalizer=v["normalizer"],
                           sheets=[dict(name="SOH", divisions_in=["MW"])],
                           exclusion_labels={"channel": "liquidation-channel store"})),
        "ros.yaml": _ROS_PROFILE,
        "norms.yaml": dict(
            role="norms", name="demo-norms", sheet="Norms", header_row=0,
            columns={"location": "Store Code", "brand_scope": "Brand",
                     "category": "Category", "qty": "Norm Qty"},
            semantics=dict(brand_scope_value="DEMO", category_values=cat_values)),
        "tiers.yaml": _TIERS_PROFILE,
    }


_MASTER_PROFILE = dict(
    role="master", name="demo-master", sheet="Stores", header_row=1,
    columns={"id": "Store Code", "name": "Store Name", "city": "City",
             "state": "State", "region": "Region", "lat": "Latitude",
             "lon": "Longitude", "model": "Store Model", "opened": "Opened On",
             "l2l": "L2L Tag", "status": "Store Status", "grade": "Grade",
             "store_type": "Store Type", "format": "Format"},
    semantics=dict(blocked_when_nonempty=["Block Reason"],
                   channel_exclude_name_contains="LIQUIDATION"))
_ROS_PROFILE = dict(
    role="ros", name="demo-ros", sheet="Velocity", header_row=0,
    columns={"location": "Store Code", "style": "Style Code", "ros8": "ROS 8W",
             "sales8": "Sales 8W", "ros_life": "ROS Life", "str_life": "STR",
             "sales_life": "Lifetime Sales", "dept": "Department",
             "shelf_life": "Shelf Life", "nsv_life": "Lifetime NSV",
             "nsv8": "NSV 8W", "inwards": "Inwards", "grn8": "GRN 8W"},
    semantics=dict(kind="base"))
_TIERS_PROFILE = dict(
    role="tiers", name="demo-tiers", header_row=0,
    columns={"location": "Store Code", "tier": "Tier"})


def write_demo(outdir, seed: int = 8, vocab: str = "tailoring") -> Path:
    """Write the full demo bundle; returns the manifest path."""
    v = VOCABS[vocab]
    out = Path(outdir)
    (out / "mappings").mkdir(parents=True, exist_ok=True)
    net = make_network(seed, vocab)

    _wb([[s["id"], s["name"], s["city"], s["state"], s["region"], s["lat"], s["lon"],
          s["model"], s["opened"], s["l2l"], s["status"], s["block"], s["grade"],
          s["stype"], s["fmt"]] for s in net["stores"]],
        ["Store Code", "Store Name", "City", "State", "Region", "Latitude",
         "Longitude", "Store Model", "Opened On", "L2L Tag", "Store Status",
         "Block Reason", "Grade", "Store Type", "Format"],
        "Stores", out / "store-master.xlsx",
        banner="DEMO STORE MASTER — export 15.07.2026")

    _wb([[r["div"], r["store"], r["cat"], v["raw_size"](r["size"]), r["style"],
          r["qty"], r["mrp_ext"], r["value"], r["season"], r["dept"]]
         for r in net["soh"]],
        ["Division", "Store Code", "Category", "Size", "Style Code", "Qty",
         "MRP", "Value", "Season", "Department"],
        "SOH", out / "soh.xlsx")

    _wb([[r["store"], r["style"], r["ros8"], r["sales8"], r["ros_life"], r["strr"],
          r["sales_life"], r["dept"], r["shelf"], r["nsv_life"], r["nsv8"],
          r["inwards"], r["grn8"]] for r in net["ros"]],
        ["Store Code", "Style Code", "ROS 8W", "Sales 8W", "ROS Life", "STR",
         "Lifetime Sales", "Department", "Shelf Life", "Lifetime NSV", "NSV 8W",
         "Inwards", "GRN 8W"],
        "Velocity", out / "velocity.xlsx")

    _wb([[r["store"], r["brand"], r["cat"], r["qty"]] for r in net["norms"]],
        ["Store Code", "Brand", "Category", "Norm Qty"],
        "Norms", out / "norms.xlsx")

    with open(out / "tiers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Store Code", "Tier"])
        for r in net["tiers"]:
            w.writerow([r["store"], r["tier"]])

    for fname, prof in _profiles(v).items():
        (out / "mappings" / fname).write_text(yaml.safe_dump(prof, sort_keys=False))

    (out / "policy.yaml").write_text(yaml.safe_dump(to_dict(demo_policy(vocab)),
                                                   sort_keys=False))
    man = dict(
        soh_asof=SOH_ASOF.isoformat(),
        roles=dict(
            master=dict(path="store-master.xlsx", profile="mappings/master.yaml"),
            soh=dict(path="soh.xlsx", profile="mappings/soh.yaml"),
            ros=[dict(path="velocity.xlsx", profile="mappings/ros.yaml")],
            norms=dict(path="norms.xlsx", profile="mappings/norms.yaml"),
            tiers=dict(path="tiers.csv", profile="mappings/tiers.yaml"),
        ))
    mp = out / "manifest.yaml"
    mp.write_text(yaml.safe_dump(man, sort_keys=False))
    return mp
