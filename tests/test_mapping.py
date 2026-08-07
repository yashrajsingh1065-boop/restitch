"""Mapping: a renamed or reordered export must never load silently wrong."""
import pytest

from restitch.io.mapping import MappingError, RoleMapping, resolve


def test_resolve_exact_case_insensitive():
    hdr = ["Store Code", " Qty ", "MRP"]
    assert resolve(hdr, "store code", "t") == 0
    assert resolve(hdr, "Qty", "t") == 1


def test_resolve_miss_names_nearest():
    with pytest.raises(MappingError, match="nearest.*'Store Code'"):
        resolve(["Store Code", "Qty"], "Store Cod", "t")


def test_unknown_canonical_field_rejected():
    with pytest.raises(MappingError, match="unknown canonical fields"):
        RoleMapping(role="soh", name="x", columns={"quantity": "Qty"})


def test_unmapped_required_field_is_loud():
    rm = RoleMapping(role="soh", name="x", columns={"qty": "Qty"})
    with pytest.raises(MappingError, match="'style' is not mapped"):
        rm.index([["Qty"]], "qty", "style")


def test_optional_field_resolves_to_none():
    rm = RoleMapping(role="soh", name="x", columns={"qty": "Qty"})
    ix = rm.index([["Qty"]], "qty", optional=("season",))
    assert ix["qty"] == 0 and ix["season"] is None


def test_store_id_one_law():
    """Canonical store identity (review M11): the same door arrives as 9001,
    9001.0 and '9001' across one tenant's files; an alphanumeric tenant's
    codes must survive verbatim, never silently emptying the network."""
    from restitch.core.ids import store_id
    assert store_id(9001) == "9001"
    assert store_id(9001.0) == "9001"
    assert store_id("9001.0") == "9001"
    assert store_id(" 9001 ") == "9001"
    assert store_id(" BLR-01 ") == "BLR-01"
    assert store_id("FW-9001") == "FW-9001"
    assert store_id("") is None
    assert store_id("   ") is None
    assert store_id(None) is None


if __name__ == "__main__":
    import sys
    mod = sys.modules["__main__"]
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        getattr(mod, name)()
        print(f"{name}: OK")
