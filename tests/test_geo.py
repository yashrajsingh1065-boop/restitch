"""Transfer geography — the geo_scope rule end to end.

Micro-fixtures: a 2-door network where exactly the geo gate decides whether the
one possible heal happens; every mode exercised, the empty-attribute law
(no scope value => no edges) asserted, the GEO-BLOCKED bucket verified.

Sweep smoke over the synthetic bundle: nesting scopes move monotonically fewer
units, every intra_city move is same-city, every max_km move is within range —
and the pan re-run prices the scope (never the blocked-count).
"""
from collections import Counter

import pytest

from restitch.core import engine
from restitch.core.model import EngineInputs, Holding, Store, canon_city, pair_km
from restitch.core.policy import Policy, from_dict, permissive, to_dict


# ── micro-fixture: one heal, one donor, geography decides ───────────────
def _micro(pol, *, donor_city="BETA", donor_state="S1", donor_region="",
           recv_region="", coords=None):
    stores = {
        1: Store(shrm=1, name="RECV", city="ALPHA", state="S1", region=recv_region),
        2: Store(shrm=2, name="DONOR", city=donor_city, state=donor_state,
                 region=donor_region),
    }
    if coords:
        (stores[1].lat, stores[1].lon), (stores[2].lat, stores[2].lon) = coords
    holdings = {
        (1, "S"): Holding(shrm=1, style="S", cat="J", div="M", sizes={"096": 1.0}),
        (2, "S"): Holding(shrm=2, style="S", cat="J", div="M", sizes={"100": 2.0}),
    }
    perf = {
        (1, "S"): dict(ros8=0.5, ros4=0.0, sales8=4.0, dept="D"),
        (2, "S"): dict(ros8=0.0, ros4=0.0, sales8=0.0, dept="D"),
    }
    inp = EngineInputs(
        stores=stores, tiers={}, holdings=holdings, excluded={},
        dept_votes={"S": Counter({"D": 3})}, perf=perf,
        style_net={"S": dict(sales8=4.0)},
        norms={(1, "J"): 2.0, (2, "J"): 1.0}, norms_context={},
        fs={}, fs_meta={})
    return inp


def _pol(**kw):
    return Policy(all_sizes=("096", "100"), pivotals=("096", "100"),
                  wave2_pivotals=(), assembly_enabled=False, **kw)


def _heal_lines(R):
    return [m for m in R["moves"] if m["program"] == "HEAL"]


def test_pan_heals_across_cities():
    pol = _pol()
    R = engine.run(_micro(pol), pol)
    assert len(_heal_lines(R)) == 1
    assert R["gap_geo"] == [] and R["geo_stat"] == dict(mode="pan", blocked_sets=0)


def test_intra_city_blocks_and_buckets():
    pol = _pol(geo_mode="intra_city")
    R = engine.run(_micro(pol), pol)
    assert _heal_lines(R) == []
    assert R["gap_geo"] == [(1, "S", ["100"])], "geo-starved heal not bucketed"
    assert R["geo_stat"] == dict(mode="intra_city", blocked_sets=1)
    assert any("GEO-BLOCKED" in b for b in R["disposition"]), \
        "disposition must name the geography, not fold it into 'lost to priority'"
    # and it must NOT read as a structural gap — the units exist
    assert R["gap_structural"] == [] and R["gap_priority"] == []


def test_city_alias_unifies_spellings():
    pol = _pol(geo_mode="intra_city", city_aliases=(("ALPHAA", "ALPHA"),))
    R = engine.run(_micro(pol, donor_city="alphaa "), pol)
    assert len(_heal_lines(R)) == 1, "alias table must make one city of two spellings"
    bare = _pol(geo_mode="intra_city")
    assert _heal_lines(engine.run(_micro(bare, donor_city="alphaa "), bare)) == []


def test_intra_state_spans_cities_within_state():
    pol = _pol(geo_mode="intra_state")
    assert len(_heal_lines(engine.run(_micro(pol), pol))) == 1
    R = engine.run(_micro(pol, donor_state="S2"), pol)
    assert _heal_lines(R) == [] and R["geo_stat"]["blocked_sets"] == 1


def test_missing_scope_attribute_means_no_edges():
    # neither store carries a region: under intra_region NOTHING is legal —
    # the stock surfaces GEO-BLOCKED instead of silently planning pan-network
    pol = _pol(geo_mode="intra_region")
    R = engine.run(_micro(pol), pol)
    assert _heal_lines(R) == [] and R["geo_stat"]["blocked_sets"] == 1
    ok = _pol(geo_mode="intra_region")
    R2 = engine.run(_micro(ok, donor_region="WEST", recv_region="WEST"), ok)
    assert len(_heal_lines(R2)) == 1


def test_max_km_gates_by_distance():
    near = ((10.0, 76.0), (10.0, 76.05))          # ~5.5 km apart
    pol = _pol(geo_mode="max_km", geo_max_km=10.0)
    assert len(_heal_lines(engine.run(_micro(pol, coords=near), pol))) == 1
    tight = _pol(geo_mode="max_km", geo_max_km=2.0)
    R = engine.run(_micro(tight, coords=near), tight)
    assert _heal_lines(R) == [] and R["geo_stat"]["blocked_sets"] == 1


def test_city_set_membership_both_ends():
    pol = _pol(geo_mode="city_set", geo_city_set=("ALPHA", "BETA"))
    assert len(_heal_lines(engine.run(_micro(pol), pol))) == 1
    out = _pol(geo_mode="city_set", geo_city_set=("ALPHA", "GAMMA"))
    R = engine.run(_micro(out), out)
    assert _heal_lines(R) == [] and R["geo_stat"]["blocked_sets"] == 1


# ── policy surface ──────────────────────────────────────────────────────
def test_geo_policy_validation():
    with pytest.raises(ValueError, match="geo_mode"):
        _pol(geo_mode="regional")
    with pytest.raises(ValueError, match="geo_city_set"):
        _pol(geo_mode="city_set")
    with pytest.raises(ValueError, match="geo_max_km"):
        _pol(geo_mode="max_km")
    with pytest.raises(ValueError, match="city_aliases"):
        _pol(city_aliases=(("ONLY-ONE-ELEMENT",),))


def test_geo_yaml_round_trip():
    pol = _pol(geo_mode="city_set", geo_city_set=("ALPHA", "BETA"),
               city_aliases=(("BENGALURU", "BANGALORE"), ("ALPHAA", "ALPHA")))
    assert from_dict(to_dict(pol)) == pol


def test_permissive_opens_the_geography():
    base = _pol(geo_mode="intra_city", city_aliases=(("X", "Y"),))
    assert permissive(base).geo_mode == "pan"


# ── sweep smoke over the synthetic bundle ───────────────────────────────
def _synth_resolved():
    import tempfile
    from pathlib import Path

    import yaml

    from restitch.cli import main
    from restitch.io.manifest import load_manifest, resolve
    if "dir" not in _SYNTH:
        d = Path(tempfile.mkdtemp(prefix="restitch-geo-"))
        import atexit
        import shutil
        atexit.register(shutil.rmtree, d, ignore_errors=True)
        assert main(["demo", "--out", str(d)]) == 0
        _SYNTH["dir"] = d
    d = _SYNTH["dir"]
    pol = from_dict(yaml.safe_load((d / "policy.yaml").read_text()))
    return resolve(load_manifest(d / "manifest.yaml"), pol)


_SYNTH: dict = {}


def _units(R):
    return sum(m["qty"] for m in R["moves"])


def test_geo_sweep_monotone_and_legal():
    _synth_resolved()          # warms the module cache the loop below reuses
    runs = {}
    for mode in ("pan", "intra_state", "intra_city"):
        rr2 = _synth_resolved()
        runs[mode] = engine.run(rr2.inputs, rr2.policy.with_(geo_mode=mode))
    assert _units(runs["pan"]) >= _units(runs["intra_state"]) \
        >= _units(runs["intra_city"]), "nesting scopes must move monotonically fewer units"
    assert _units(runs["intra_city"]) > 0, \
        "synthetic cities hold 3-4 doors; intra-city must still heal"
    stores = runs["intra_city"]["stores"]
    for m in runs["intra_city"]["moves"]:
        assert canon_city(stores[m["src"]].city) == canon_city(stores[m["dst"]].city)


def test_max_km_moves_stay_in_range():
    rr = _synth_resolved()
    pol = rr.policy.with_(geo_mode="max_km", geo_max_km=120.0)
    R = engine.run(rr.inputs, pol)
    tk = pol.tier_km
    for m in R["moves"]:
        assert pair_km(R["stores"], m["src"], m["dst"], tk) <= 120.0 + 1e-6


def test_scope_priced_by_rerun_not_by_blocks():
    from restitch.core.summary import scope_cost
    a, b = _synth_resolved(), _synth_resolved()
    R_city = engine.run(a.inputs, a.policy.with_(geo_mode="intra_city"))
    R_pan = engine.run(b.inputs, b.policy.with_(geo_mode="pan"))
    cost = scope_cost(R_city, R_pan)
    assert cost["mode"] == "intra_city"
    assert cost["units_cost"] == _units(R_pan) - _units(R_city) >= 0
    assert cost["sets_cost"] >= 0
    assert cost["cut_full"] >= cost["cut_full_pan"] - 1e-9, \
        "an open network must end at least as healed as a scoped one"


if __name__ == "__main__":
    import sys
    mod = sys.modules["__main__"]
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        getattr(mod, name)()
        print(f"{name}: OK")
