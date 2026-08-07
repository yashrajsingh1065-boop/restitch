"""SizeLadder generalization — named normalizers and NAMED extremes.

Extreme sizes are ladder members declared by the profile, not numeric
thresholds: a threshold cannot classify an alpha ladder (XS…3XL) or a
half-size shoe ladder, and it silently guesses about sizes it has never seen.
A size in neither extreme set — unknown sizes included — loads, renders and
counts, but never EXT-moves.
"""
from collections import Counter

import pytest

from restitch.core import engine
from restitch.core.model import (
    EngineInputs,
    Holding,
    Store,
    normalize_size_alpha,
    normalize_size_numeric3,
    normalize_size_uk_shoe,
)
from restitch.core.policy import Policy, from_dict, to_dict


# ── normalizers ─────────────────────────────────────────────────────────
def test_numeric_zfill3():
    assert normalize_size_numeric3(96) == "096"
    assert normalize_size_numeric3(" 104 ") == "104"
    assert normalize_size_numeric3("XL") == "XL"


def test_uk_shoe_keeps_the_half_size():
    assert normalize_size_uk_shoe(10) == "10"
    assert normalize_size_uk_shoe("10.0") == "10"
    assert normalize_size_uk_shoe(10.5) == "10.5"
    assert normalize_size_uk_shoe(" 6,5 ") == "6.5"
    assert normalize_size_uk_shoe("uk 8") == "UK 8"


def test_alpha():
    assert normalize_size_alpha(" xl ") == "XL"


def test_loaders_registry_has_all_four():
    from restitch.io.loaders import NORMALIZERS
    assert set(NORMALIZERS) == {"numeric_zfill3", "uk_shoe", "alpha", "verbatim"}


# ── named extremes on an alpha ladder ───────────────────────────────────
def _alpha_net(donor_sizes):
    stores = {
        1: Store(shrm=1, name="DONOR", city="ALPHA", state="S1"),
        2: Store(shrm=2, name="SELLER", city="BETA", state="S1"),
    }
    holdings = {
        (1, "S"): Holding(shrm=1, style="S", cat="J", div="M",
                          sizes=dict(donor_sizes)),
        (2, "S"): Holding(shrm=2, style="S", cat="J", div="M",
                          sizes={"M": 1.0, "L": 1.0}),
    }
    perf = {
        (1, "S"): dict(ros8=0.0, ros4=0.0, sales8=0.0, dept="D"),
        (2, "S"): dict(ros8=0.5, ros4=0.0, sales8=4.0, dept="D"),
    }
    return EngineInputs(
        stores=stores, tiers={}, holdings=holdings, excluded={},
        dept_votes={"S": Counter({"D": 3})}, perf=perf,
        style_net={"S": dict(sales8=4.0)},
        norms={(1, "J"): 1.0, (2, "J"): 3.0}, norms_context={},
        fs={}, fs_meta={})


def _alpha_pol(**kw):
    return Policy(all_sizes=("S", "M", "L", "XL"), pivotals=("M", "L"),
                  wave2_pivotals=(), assembly_enabled=False, **kw)


def test_named_extreme_moves_on_alpha_ladder():
    pol = _alpha_pol(ext_hi_sizes=("XL",))
    R = engine.run(_alpha_net({"M": 1.0, "XL": 2.0}), pol)
    ext = [m for m in R["moves"] if m["program"] == "EXT"]
    assert ext and all(m["size"] == "XL" and m["dst"] == 2 for m in ext)


def test_unnamed_size_never_ext_moves():
    pol = _alpha_pol()               # no extremes declared
    R = engine.run(_alpha_net({"M": 1.0, "XL": 2.0}), pol)
    assert [m for m in R["moves"] if m["program"] == "EXT"] == []


def test_unknown_size_loads_but_never_ext_moves():
    # '3XL' is outside the declared ladder AND unnamed: it must count in units
    # yet never travel as an extreme
    pol = _alpha_pol(ext_hi_sizes=("XL",))
    R = engine.run(_alpha_net({"M": 1.0, "3XL": 5.0}), pol)
    assert R["holdings"][(1, "S")].units == 6.0
    assert [m for m in R["moves"] if m["size"] == "3XL"] == []


def test_unknown_sizes_block_above_the_threshold():
    # 5 of 6 units off-ladder (83%) — this is a wrong normalizer, not noise;
    # review M3: below the threshold it stays amber, above it blocks
    from restitch.io.sanity import run_sanity
    pol = _alpha_pol(ext_hi_sizes=("XL",))
    findings = run_sanity(_alpha_net({"M": 1.0, "3XL": 5.0}), pol)
    hit = [f for f in findings if f.id == "unknown_sizes"]
    assert hit and hit[0].level == "red" and "'3XL'" in hit[0].detail


def test_unknown_sizes_small_share_stays_amber():
    from restitch.io.sanity import run_sanity
    pol = _alpha_pol(ext_hi_sizes=("XL",))
    findings = run_sanity(_alpha_net({"M": 9.0, "XL": 2.0, "3XL": 1.0}), pol)
    hit = [f for f in findings if f.id == "unknown_sizes"]
    assert hit and hit[0].level == "amber"


def test_int_typed_vocabulary_is_refused_and_coerced():
    # review M3: Policy(pivotals=(7, 8, 9)) once constructed silently and
    # produced a near-empty plan that proved itself perfectly
    import pytest

    from restitch.core.policy import from_dict, to_dict
    with pytest.raises(ValueError, match="must be strings"):
        Policy(all_sizes=(6, 7, 8, 9), pivotals=(7, 8, 9),
               wave2_pivotals=(), assembly_enabled=False)
    pol = from_dict(dict(all_sizes=[6, 6.5, 7], pivotals=[6, 7],
                         wave2_pivotals=[], assembly_enabled=False))
    assert pol.all_sizes == ("6", "6.5", "7") and pol.pivotals == ("6", "7")
    assert from_dict(to_dict(pol)) == pol


def test_empty_universe_is_a_red_finding():
    from restitch.io.sanity import run_sanity
    inp = EngineInputs(stores={}, tiers={}, holdings={}, excluded={},
                       dept_votes={}, perf={}, style_net={}, norms={},
                       norms_context={}, fs={}, fs_meta={})
    findings = run_sanity(inp, _alpha_pol())
    assert [f.id for f in findings] == ["no_stock"]
    assert findings[0].level == "red"


# ── policy surface ──────────────────────────────────────────────────────
def test_pivotal_cannot_be_extreme():
    with pytest.raises(ValueError, match="pivotals"):
        _alpha_pol(ext_lo_sizes=("M",))


def test_extremes_round_trip():
    pol = _alpha_pol(ext_lo_sizes=("S",), ext_hi_sizes=("XL",))
    assert from_dict(to_dict(pol)) == pol


if __name__ == "__main__":
    import sys
    mod = sys.modules["__main__"]
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        getattr(mod, name)()
        print(f"{name}: OK")
