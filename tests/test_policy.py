"""Policy: validation is loud, round-trip is exact."""
import pytest

from restitch.core.policy import Policy, from_dict, permissive, to_dict


def _base(**kw):
    d = dict(all_sizes=("084", "088", "092", "096", "100"),
             pivotals=("088", "092", "096"), wave2_pivotals=("092", "096"))
    d.update(kw)
    return Policy(**d)


def test_round_trip_exact():
    p = _base(core_styles=frozenset({"AAA1", "BBB2"}))
    assert from_dict(to_dict(p)) == p


def test_unknown_key_rejected_with_suggestion():
    with pytest.raises(ValueError, match="pivotals"):
        from_dict({"pivotols": ["088", "092"]})


def test_too_few_pivotals():
    with pytest.raises(ValueError, match="pivotals"):
        _base(pivotals=("092",), wave2_pivotals=())


def test_wave2_must_be_strict_subset():
    with pytest.raises(ValueError, match="strict subset"):
        _base(wave2_pivotals=("088", "092", "096"))


def test_floor_ordering():
    with pytest.raises(ValueError, match="transit_floor"):
        _base(transit_floor=0.9, fill_floor=0.8)


def test_uplift_below_one_rejected():
    with pytest.raises(ValueError, match="downhill"):
        _base(ros_uplift=0.9)


def test_assembly_needs_wave2():
    with pytest.raises(ValueError, match="assembly"):
        _base(wave2_pivotals=(), assembly_enabled=True)


def test_permissive_still_validates():
    p = permissive(_base())
    assert p.fill_floor == 0.0 and p.movement_budget >= 10**9
    assert p.pivotals == ("088", "092", "096")   # vocabulary survives


def test_with_revalidates():
    with pytest.raises(ValueError):
        _base().with_(recv_peak_cap=0.5)


if __name__ == "__main__":
    import sys
    mod = sys.modules["__main__"]
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        getattr(mod, name)()
        print(f"{name}: OK")
