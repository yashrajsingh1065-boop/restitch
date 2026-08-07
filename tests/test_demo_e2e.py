"""End-to-end over the synthetic bundle: files -> profiles -> sanity -> engine
-> plan.json -> verify. Also determinism (double-run identical) and the
permissive-dominates-constrained ceiling property.
"""
import json
import tempfile
from pathlib import Path

import yaml

_DIR = None


def _demo_dir() -> Path:
    global _DIR
    if _DIR is None:
        _DIR = Path(tempfile.mkdtemp(prefix="restitch-test-"))
        import atexit
        import shutil
        atexit.register(shutil.rmtree, _DIR, ignore_errors=True)
        from restitch.cli import main
        assert main(["demo", "--out", str(_DIR)]) == 0
    return _DIR


def _resolved():
    from restitch.core.policy import from_dict
    from restitch.io.manifest import load_manifest, resolve
    d = _demo_dir()
    return resolve(load_manifest(d / "manifest.yaml"),
                   from_dict(yaml.safe_load((d / "policy.yaml").read_text())))


def test_demo_run_dir_is_complete():
    d = _demo_dir()
    for f in ("plan.json", "provenance.json", "sanity.txt"):
        assert (d / "run" / f).exists(), f"missing {f}"
    plan = json.loads((d / "run" / "plan.json").read_text())
    assert plan["metrics"]["lines"] == len(plan["moves"]) > 0
    prov = json.loads((d / "run" / "provenance.json").read_text())
    assert set(prov["files"]) == {"master", "soh", "ros", "norms", "tiers"}
    assert all(f2["sha256"] for fl in prov["files"].values() for f2 in fl)
    assert prov["policy"]["pivotals"] == ["088", "092", "096"]
    # the planted bulk outlier must be caught
    assert "bulk_outlier" in (d / "run" / "sanity.txt").read_text()


def test_verify_reproduces_the_plan():
    from restitch.cli import main
    assert main(["verify", str(_demo_dir() / "run")]) == 0


def test_determinism_double_resolve():
    from restitch.core import engine
    from restitch.core.summary import canonical_moves
    a, b = _resolved(), _resolved()
    ma = canonical_moves(engine.run(a.inputs, a.policy))
    mb = canonical_moves(engine.run(b.inputs, b.policy))
    assert ma == mb and len(ma) > 0


def test_every_move_endpoint_is_a_stocked_door():
    from restitch.core import engine
    rr = _resolved()
    R = engine.run(rr.inputs, rr.policy)
    for m in R["moves"]:
        assert m["src"] in R["sb_stores"] and m["dst"] in R["sb_stores"]
        assert m["qty"] >= 1 and m["src"] != m["dst"]


def test_permissive_dominates_constrained():
    from restitch.core import engine
    from restitch.core.policy import permissive
    rr = _resolved()
    units = sum(m["qty"] for m in engine.run(rr.inputs, rr.policy)["moves"])
    rp = _resolved()
    ceiling = sum(m["qty"] for m in
                  engine.run(rp.inputs, permissive(rp.policy))["moves"])
    assert ceiling >= units, f"guards moved MORE than no-guards: {units} > {ceiling}"


def test_degradation_notes_are_explicit():
    rr = _resolved()
    joined = "\n".join(rr.notes)
    assert "fs" in joined and "norms_context" in joined
    assert "derived new_door_cutoff" in joined
    assert "derived season_current_index" in joined


if __name__ == "__main__":
    import sys
    mod = sys.modules["__main__"]
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        getattr(mod, name)()
        print(f"{name}: OK")
