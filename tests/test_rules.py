"""Rule registry — one id, whole proof chain, skipped never silent.

Meta-tests hold the registry to its own law: every engine invariant is owned
by the kernel or by exactly-registered rules, every rule's toggle is a real
Policy predicate, and a disabled rule's proof surface shows up as skipped —
a shrunk battery and a passing battery must never look alike.
"""
from collections import Counter

import pytest

from restitch.core import engine
from restitch.core.checks import Battery
from restitch.core.model import EngineInputs, Holding, Store
from restitch.core.policy import Policy, band_atoms, permissive
from restitch.core.rules import KERNEL_INVARIANTS, RULES, rule, rule_status


def _pol(**kw):
    return Policy(all_sizes=("096", "100"), pivotals=("096", "100"),
                  wave2_pivotals=(), assembly_enabled=False, **kw)


# ── registry meta ───────────────────────────────────────────────────────
def test_every_invariant_is_owned():
    owned = set(KERNEL_INVARIANTS)
    for r in RULES:
        owned |= set(r.invariants)
    assert owned == set(range(1, 30)), \
        f"unowned/phantom invariants: {owned ^ set(range(1, 30))}"


def test_rule_ids_unique_and_lookup_works():
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids))
    assert rule("geo_scope").invariants == (29,)


def test_every_rule_has_a_doc_and_a_proof_surface():
    for r in RULES:
        assert r.doc.strip(), f"{r.id} carries no buyer directive"
        assert r.invariants, f"{r.id} owns no invariant — an unproven rule"


def test_permissive_disables_the_guards_not_the_machinery():
    p = permissive(_pol(geo_mode="intra_city"))
    st = dict((i, en) for i, _t, en in rule_status(p))
    for guard in ("style_freeze", "door_freeze_afs", "new_door", "floors",
                  "peak_cap", "style_depth", "ros_gates", "fofo_net_flow",
                  "geo_scope"):
        assert not st[guard], f"{guard} should be OFF under permissive"
    for machinery in ("localize", "fresh", "no_norm", "door_freeze_master"):
        assert st[machinery], f"{machinery} should stay ON under permissive"


# ── battery mechanics ───────────────────────────────────────────────────
def test_battery_counts_and_summary():
    b = Battery()
    b.add("a.one", "s", "A", True)
    b.add("a.two", "s", "A", False, "broke")
    b.skip("b.one", "s", "B", "off")
    assert (b.counts(), b.ok) == ((1, 1, 1), False)
    assert [c.id for c in b.failed()] == ["a.two"]
    assert b.summary() == "3 checks: 1 passed, 1 FAILED, 1 skipped"


def test_battery_refuses_duplicate_ids():
    b = Battery()
    b.add("x.y", "s", "X", True)
    with pytest.raises(ValueError, match="duplicate"):
        b.add("x.y", "s", "X", True)
    with pytest.raises(ValueError, match="duplicate"):
        b.skip("x.y", "s", "X", "off")


# ── the banding law (review M1) ─────────────────────────────────────────
def test_band_atoms_skip_fill_is_the_law():
    # 9 no longer fits after... 9 fits P1 (cum 9); 7 exceeds 10 -> defers to
    # P3 WITHOUT consuming; 5 fills the P2 window behind it (cum 14 <= 15)
    assert band_atoms([9, 7, 5], 10, 5) == ["P1", "P3", "P2"]
    assert band_atoms([10], 10, 0) == ["P1"], "an atom exactly at budget funds"
    assert band_atoms([11], 10, 0) == ["P3"]
    assert band_atoms([], 10, 5) == []


def test_battery_accepts_every_skip_fill_banding(tmp_path):
    # the review demonstrated 73 lever configs where the battery refused the
    # engine's own plan; the shared band_atoms law must end that class
    import yaml

    from restitch.core.checks import build_recon, run_battery
    from restitch.core.policy import from_dict
    from restitch.io.manifest import load_manifest, resolve
    from restitch.synth import write_demo
    man = write_demo(tmp_path, seed=8)
    base = yaml.safe_load((tmp_path / "policy.yaml").read_text())
    for budget, extra in ((7, 0), (13, 3), (29, 5), (61, 10)):
        rr = resolve(load_manifest(man),
                     from_dict({**base, "movement_budget": budget,
                                "budget_p2_extra": extra}))
        R = engine.run(rr.inputs, rr.policy)
        rows = R["moves"]
        for x in rows:
            x.setdefault("same_city", False)
        battery = run_battery(R, rows, build_recon(R), rr.policy)
        assert battery.ok, (budget, extra, [c.id for c in battery.failed()])


# ── invariant gating (review M2) ────────────────────────────────────────
def _micro_inputs():
    stores = {1: Store(shrm=1, name="R", city="A", state="S"),
              2: Store(shrm=2, name="D", city="B", state="S")}
    holdings = {
        (1, "S"): Holding(shrm=1, style="S", cat="J", div="M",
                          sizes={"096": 1.0, "100": 1.0}),
        (2, "S"): Holding(shrm=2, style="S", cat="J", div="M", sizes={"100": 2.0}),
    }
    perf = {(1, "S"): dict(ros8=0.5, ros4=0.0, sales8=4.0, dept="D"),
            (2, "S"): dict(ros8=0.5, ros4=0.0, sales8=0.0, dept="D")}
    return EngineInputs(stores=stores, tiers={}, holdings=holdings, excluded={},
                        dept_votes={"S": Counter({"D": 3})}, perf=perf,
                        style_net={"S": dict(sales8=4.0)},
                        norms={(1, "J"): 2.0, (2, "J"): 1.0}, norms_context={},
                        fs={}, fs_meta={})


def test_permissive_tied_ros_no_longer_crashes():
    # review M2's crash case: donor and receiver tied at RoS 0.5 under the
    # permissive costing ceiling — TOPUP's strict-climb gate now refuses the
    # tie instead of producing a move invariant 7 asserts on
    R = engine.run(_micro_inputs(), permissive(_pol()))
    for m in R["moves"]:
        if m["program"] in ("SET", "TOPUP", "EXT"):
            assert m["dst_ros"] > m["src_ros"]


# ── normless containment (review M4) ────────────────────────────────────
def test_evac_off_freezes_normless_doors_instead_of_unconstraining_them():
    def net():
        stores = {1: Store(shrm=1, name="R", city="A", state="S"),
                  2: Store(shrm=2, name="D", city="B", state="S")}
        holdings = {
            (1, "S"): Holding(shrm=1, style="S", cat="J", div="M", sizes={"096": 1.0}),
            (2, "S"): Holding(shrm=2, style="S", cat="J", div="M", sizes={"100": 3.0}),
        }
        perf = {(1, "S"): dict(ros8=0.5, ros4=0.0, sales8=4.0, dept="D"),
                (2, "S"): dict(ros8=0.0, ros4=0.0, sales8=0.0, dept="D")}
        return EngineInputs(stores=stores, tiers={}, holdings=holdings,
                            excluded={}, dept_votes={"S": Counter({"D": 3})},
                            perf=perf, style_net={"S": dict(sales8=4.0)},
                            norms={(1, "J"): 2.0}, norms_context={},   # door 2 normless
                            fs={}, fs_meta={})

    R_on = engine.run(net(), _pol())                     # evacuation on: donates
    assert any(m["src"] == 2 for m in R_on["moves"])
    R_off = engine.run(net(), _pol(evac_no_norm=False))  # off: frozen, not free
    assert 2 in R_off["frozen"]
    assert not any(2 in (m["src"], m["dst"]) for m in R_off["moves"])


# ── engine surfacing ────────────────────────────────────────────────────
def test_engine_reports_rule_status():
    stores = {1: Store(shrm=1, name="R", city="A", state="S"),
              2: Store(shrm=2, name="D", city="B", state="S")}
    holdings = {
        (1, "S"): Holding(shrm=1, style="S", cat="J", div="M", sizes={"096": 1.0}),
        (2, "S"): Holding(shrm=2, style="S", cat="J", div="M", sizes={"100": 2.0}),
    }
    perf = {(1, "S"): dict(ros8=0.5, ros4=0.0, sales8=4.0, dept="D"),
            (2, "S"): dict(ros8=0.0, ros4=0.0, sales8=0.0, dept="D")}
    inp = EngineInputs(stores=stores, tiers={}, holdings=holdings, excluded={},
                       dept_votes={"S": Counter({"D": 3})}, perf=perf,
                       style_net={"S": dict(sales8=4.0)},
                       norms={(1, "J"): 2.0, (2, "J"): 1.0}, norms_context={},
                       fs={}, fs_meta={})
    R = engine.run(inp, _pol())
    assert {i for i, _t, en in R["rule_status"] if not en} >= {"wave2", "assembly"}
    from restitch.core.summary import text_summary
    txt = text_summary(R)
    assert "rules:" in txt and "skipped" in txt


if __name__ == "__main__":
    import sys
    mod = sys.modules["__main__"]
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        getattr(mod, name)()
        print(f"{name}: OK")
