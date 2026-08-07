"""P3 gate — TestClient end to end over the synthetic bundle.

upload → (deliberate mapping mismatch → fix) → sanity acknowledge → levers →
review → execute → poll to done → checks-first report → workbook download →
second run with one lever changed → compare. Plus: a broken run lands as a
failed status carrying the assertion text, never a dead server.
"""
import json
import re
import tempfile
import time
from pathlib import Path

import openpyxl
import yaml
from fastapi.testclient import TestClient

from restitch.web import create_app
from restitch.web.runner import execute_run, read_status

_ENV: dict = {}


def _env():
    if not _ENV:
        import atexit
        import shutil

        from restitch.cli import main
        bundle = Path(tempfile.mkdtemp(prefix="restitch-webdemo-"))
        # demo also runs the CLI pipeline; we only need the files, but the run
        # doubles as a fixture check that the bundle itself is healthy
        assert main(["demo", "--out", str(bundle)]) == 0
        root = Path(tempfile.mkdtemp(prefix="restitch-webruns-"))
        for d in (bundle, root):
            atexit.register(shutil.rmtree, d, ignore_errors=True)
        _ENV.update(bundle=bundle, root=root, client=TestClient(create_app(root)))
    return _ENV


def _run_a(client, bundle) -> str:
    """rid_a on demand — tests must not depend on execution order."""
    if "rid_a" not in _ENV:
        rid = _new_run(client)
        r = client.post(f"/runs/{rid}/inputs", files=_files_payload(bundle),
                        data={"soh_asof": "2026-07-15"}, follow_redirects=False)
        assert r.headers["location"].endswith("sanity")
        st = _drive_to_done(client, rid)
        assert st["state"] == "done", st.get("error")
        _ENV["rid_a"] = rid
    return _ENV["rid_a"]


def _files_payload(bundle: Path, soh_profile_text: str | None = None):
    def f(p):
        return (p.name, p.read_bytes())

    soh_prof = (soh_profile_text.encode() if soh_profile_text
                else (bundle / "mappings" / "soh.yaml").read_bytes())
    return {
        "soh": f(bundle / "soh.xlsx"),
        "soh_profile": ("soh.yaml", soh_prof),
        "master": f(bundle / "store-master.xlsx"),
        "master_profile": f(bundle / "mappings" / "master.yaml"),
        "norms": f(bundle / "norms.xlsx"),
        "norms_profile": f(bundle / "mappings" / "norms.yaml"),
        "ros": f(bundle / "velocity.xlsx"),
        "ros_profile": f(bundle / "mappings" / "ros.yaml"),
        "tiers": f(bundle / "tiers.csv"),
        "tiers_profile": f(bundle / "mappings" / "tiers.yaml"),
        "policy": f(bundle / "policy.yaml"),
    }


def _new_run(client) -> str:
    r = client.post("/runs", follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].split("/")[2]


def _drive_to_done(client, rid, overrides=None, timeout=120):
    r = client.post(f"/runs/{rid}/sanity", data={"ack": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("configure")
    r = client.post(f"/runs/{rid}/configure", data=overrides or {},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("review")
    r = client.get(f"/runs/{rid}/review")
    assert r.status_code == 200
    r = client.post(f"/runs/{rid}/execute", follow_redirects=False)
    assert r.status_code == 303
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/runs/{rid}/status").json()
        if st["state"] in ("done", "failed"):
            return st
        time.sleep(0.5)
    raise AssertionError("run did not finish in time")


def test_e2e_upload_map_sanity_levers_run_report_download():
    env = _env()
    client, bundle = env["client"], env["bundle"]
    rid = _new_run(client)

    # deliberately break the soh profile: the mapping page must surface,
    # suggestions shown, and the corrected profile must unblock the load
    prof = yaml.safe_load((bundle / "mappings" / "soh.yaml").read_text())
    good_qty = prof["columns"]["qty"]
    prof["columns"]["qty"] = "Quantityy"
    r = client.post(f"/runs/{rid}/inputs",
                    files=_files_payload(bundle, yaml.safe_dump(prof)),
                    data={"soh_asof": "2026-07-15"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("map/soh")
    page = client.get(f"/runs/{rid}/map/soh")
    assert "Quantityy" in page.text and "Qty" in page.text, \
        "mapping page must show the failure and the file's own headers"
    prof["columns"]["qty"] = good_qty
    r = client.post(f"/runs/{rid}/map/soh",
                    data={"profile_yaml": yaml.safe_dump(prof)},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("sanity")

    # sanity page carries the planted bulk outlier as amber; ack is required
    page = client.get(f"/runs/{rid}/sanity")
    assert "bulk_outlier" in page.text
    r = client.post(f"/runs/{rid}/sanity", data={}, follow_redirects=False)
    assert r.status_code == 200 and "Acknowledge" in r.text

    st = _drive_to_done(client, rid)
    assert st["state"] == "done", st.get("error")
    assert "checks" in st["headline"]

    page = client.get(f"/runs/{rid}")
    assert "Build battery" in page.text and "Independent verify" in page.text
    assert "PASS" in page.text and "SKIPPED" in page.text
    assert page.text.index("battery") < page.text.index("Download movement.xlsx"), \
        "checks come before the download — trust is the product"

    wb_bytes = client.get(f"/runs/{rid}/download").content
    tmp = Path(tempfile.mkdtemp()) / "dl.xlsx"
    tmp.write_bytes(wb_bytes)
    wb = openpyxl.load_workbook(tmp, read_only=True)
    assert len(wb.sheetnames) == 13
    _ENV["rid_a"] = rid


def test_lever_override_prices_itself_in_compare():
    env = _env()
    client, bundle = env["client"], env["bundle"]
    rid_b = _new_run(client)
    r = client.post(f"/runs/{rid_b}/inputs", files=_files_payload(bundle),
                    data={"soh_asof": "2026-07-15"}, follow_redirects=False)
    assert r.headers["location"].endswith("sanity")
    st = _drive_to_done(client, rid_b, overrides={"movement_budget": "600"})
    assert st["state"] == "done", st.get("error")

    # the override is visible on the report as a diff vs preset
    page = client.get(f"/runs/{rid_b}")
    assert "movement_budget" in page.text and "600" in page.text

    rid_a = _run_a(client, bundle)
    cmp_ = env["client"].get(f"/compare?a={rid_a}&b={rid_b}")
    assert cmp_.status_code == 200 and "Units moved" in cmp_.text

    # runs list shows both with headline metrics
    home = client.get("/")
    assert rid_a in home.text and rid_b in home.text and "cut" in home.text


def test_dead_worker_never_reads_as_running_forever():
    env = _env()
    root = env["root"]
    d = root / "dead-worker"
    (d / "out").mkdir(parents=True)
    (d / "status.json").write_text(json.dumps(
        {"state": "running", "pid": 2 ** 22 + 12345}))   # a pid that cannot exist
    st = env["client"].get("/runs/dead-worker/status").json()
    assert st["state"] == "failed" and "died before reporting" in st["error"]


def test_foreign_pid_fails_over_instead_of_500ing_every_page():
    # review M5a: pid 1 raised PermissionError through liveness and took down
    # the runs list, the run page and the status poll — permanently
    env = _env()
    d = env["root"] / "foreign-pid"
    (d / "out").mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "running", "pid": 1}))
    st = env["client"].get("/runs/foreign-pid/status").json()
    assert st["state"] == "failed" and "reused or foreign" in st["error"]
    assert env["client"].get("/").status_code == 200


def test_execute_refuses_mid_flow_and_double_starts():
    # review M5d: POST /execute from any state once spawned a second worker
    env = _env()
    client = env["client"]
    rid = _new_run(client)                    # state: files — nothing uploaded
    r = client.post(f"/runs/{rid}/execute", follow_redirects=False)
    assert r.status_code == 303
    st = client.get(f"/runs/{rid}/status").json()
    assert st["state"] == "files", "execute must not fire outside review/failed/done"


def test_overrule_does_not_survive_new_findings():
    # review M5e: an overrule recorded against v1 findings authorized new,
    # different reds — the child now matches the findings hash
    from restitch.io.sanity import Finding
    from restitch.web.runner import red_hash
    a = [Finding("x", "red", "first problem")]
    b = [Finding("y", "red", "different problem")]
    assert red_hash(a) != red_hash(b)
    assert red_hash([vars(f) for f in a]) == red_hash(a), \
        "hash must agree between live findings and their JSON form"


def test_rerun_carries_profiles_and_asks_only_for_raw_files():
    env = _env()
    client, bundle = env["client"], env["bundle"]
    rid_a = _run_a(client, bundle)
    r = client.post(f"/runs/{rid_a}/rerun", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("inputs")
    nid = r.headers["location"].split("/")[2]
    assert nid != rid_a
    page = client.get(f"/runs/{nid}/inputs")
    assert f"Rerun of {rid_a}" in page.text
    assert "leave empty to reuse" in page.text
    # only fresh raw files and the date — profiles and policy carried over
    raws = {k: v for k, v in _files_payload(bundle).items()
            if not k.endswith("_profile") and k != "policy"}
    r = client.post(f"/runs/{nid}/inputs", files=raws,
                    data={"soh_asof": "2026-07-15"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("sanity")
    st = _drive_to_done(client, nid)
    assert st["state"] == "done", st.get("error")
    # same files through the same profiles and levers -> the same plan
    a = client.get(f"/runs/{rid_a}/status").json()
    assert st["headline"]["units"] == a["headline"]["units"]
    _ENV["rid_rerun"] = nid


def test_rerun_refused_on_a_run_with_nothing_reusable():
    env = _env()
    client = env["client"]
    rid = _new_run(client)                    # stub: no policy, no profiles
    r = client.post(f"/runs/{rid}/rerun", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/runs/{rid}", \
        "a stub has nothing to clone — bounce back to the run, spawn nothing"


def _dup_of_a(client, bundle) -> str:
    """Duplicate of run A with only movement_budget changed — on demand."""
    if "rid_dup" not in _ENV:
        rid_a = _run_a(client, bundle)
        r = client.post(f"/runs/{rid_a}/duplicate", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("configure")
        nid = r.headers["location"].split("/")[2]
        r = client.post(f"/runs/{nid}/configure",
                        data={"movement_budget": "500"}, follow_redirects=False)
        assert r.headers["location"].endswith("review")
        client.post(f"/runs/{nid}/execute", follow_redirects=False)
        t0 = time.time()
        while time.time() - t0 < 120:
            st = client.get(f"/runs/{nid}/status").json()
            if st["state"] in ("done", "failed"):
                break
            time.sleep(0.5)
        assert st["state"] == "done", st.get("error")
        _ENV["rid_dup"] = nid
    return _ENV["rid_dup"]


def test_duplicate_opens_at_levers_with_inputs_cloned():
    env = _env()
    client, bundle, root = env["client"], env["bundle"], env["root"]
    rid_a = _run_a(client, bundle)
    nid = _dup_of_a(client, bundle)
    # the inputs are the SAME bytes — provenance hashes must agree role by role
    def prints(rid):
        prov = json.loads((root / rid / "out" / "provenance.json").read_text())
        return {role: sorted(f["sha256"] for f in fs)
                for role, fs in prov["files"].items()}
    assert prints(nid) == prints(rid_a)


def test_compare_proves_input_identity_and_diffs_the_levers():
    env = _env()
    client, bundle = env["client"], env["bundle"]
    rid_a = _run_a(client, bundle)
    rid_dup = _dup_of_a(client, bundle)
    page = client.get(f"/compare?a={rid_a}&b={rid_dup}")
    assert page.status_code == 200
    assert "Inputs identical" in page.text, \
        "a duplicate ran on the same bytes — the banner must prove it"
    assert "movement_budget" in page.text and "500" in page.text, \
        "the lever diff must name the one changed lever"
    # the delta column speaks in words and units, not hue alone
    assert "better" in page.text or "worse" in page.text or "no change" in page.text
    assert "· " in page.text


def test_inputs_verdict_states():
    from restitch.web.app import inputs_verdict
    fa = {"soh": ["aa"], "master": ["bb"]}
    assert inputs_verdict(fa, dict(fa)) == ("identical", [], 2)
    state, roles, _ = inputs_verdict(fa, {"soh": ["aa"], "master": ["XX"]})
    assert state == "differ" and roles == ["master"]
    assert inputs_verdict(None, fa)[0] == "unknown"


def test_mapping_dropdowns_fix_the_profile_without_writing_yaml():
    env = _env()
    client, bundle, root = env["client"], env["bundle"], env["root"]
    rid = _new_run(client)
    good = yaml.safe_load((bundle / "mappings" / "soh.yaml").read_text())
    broken = dict(good, columns=dict(good["columns"], qty="Quantityy"))
    r = client.post(f"/runs/{rid}/inputs",
                    files=_files_payload(bundle, yaml.safe_dump(broken)),
                    data={"soh_asof": "2026-07-15"}, follow_redirects=False)
    assert r.headers["location"].endswith("map/soh")

    page = client.get(f"/runs/{rid}/map/soh")
    assert '<select id="col_qty" name="col_qty">' in page.text, \
        "every canonical field gets a dropdown of the file's own headers"
    assert "Quantityy (not in file)" in page.text, \
        "the bad mapping stays visible, flagged, so the user sees what broke"
    assert "Advanced" in page.text and "profile_yaml" in page.text

    # fix via the dropdowns alone — no YAML in the form
    data = {"sheet": good.get("sheet") or "",
            "header_row": str(good.get("header_row", 0))}
    data.update({f"col_{k}": v for k, v in good["columns"].items()})
    r = client.post(f"/runs/{rid}/map/soh", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("sanity"), \
        client.get(f"/runs/{rid}/map/soh").text[:600]

    # the server-side rewrite must not touch what the dropdowns don't own
    after = yaml.safe_load((root / rid / "mappings" / "soh.yaml").read_text())
    assert after.get("semantics") == good.get("semantics"), \
        "semantics survive the dropdown path untouched"
    assert after["columns"] == good["columns"]


def test_lever_rejection_names_the_lever_and_keeps_what_was_typed():
    env = _env()
    client, bundle = env["client"], env["bundle"]
    rid_a = _run_a(client, bundle)
    r = client.post(f"/runs/{rid_a}/duplicate", follow_redirects=False)
    nid = r.headers["location"].split("/")[2]
    r = client.post(f"/runs/{nid}/configure",
                    data={"movement_budget": "abc", "fill_floor": "0.9"})
    assert r.status_code == 200
    assert "Movement budget" in r.text and "whole number" in r.text, \
        "the rejection names the lever by its label, not int()'s traceback"
    assert 'value="abc"' in r.text, "the bad value re-renders for correction"
    assert 'value="0.9"' in r.text, "valid siblings survive the rejection"
    # levers get real label-for wiring
    assert 'for="lv-movement_budget"' in r.text
    assert 'id="lv-movement_budget"' in r.text
    # cross-field policy law still rejects — and ALSO keeps what was typed
    # (demo preset transit_floor 0.75 makes fill_floor 0.5 unlawful)
    r = client.post(f"/runs/{nid}/configure",
                    data={"movement_budget": "700", "fill_floor": "0.5"})
    assert r.status_code == 200 and "transit_floor" in r.text
    assert 'value="0.5"' in r.text and 'value="700"' in r.text
    # corrected post proceeds
    r = client.post(f"/runs/{nid}/configure",
                    data={"movement_budget": "700", "fill_floor": "0.9"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("review")


def test_report_carries_provenance_counts_and_no_failure_hoist_when_green():
    env = _env()
    client, bundle, root = env["client"], env["bundle"], env["root"]
    rid = _run_a(client, bundle)
    page = client.get(f"/runs/{rid}")
    # provenance sub-bar: as-of date, preset name, sha-stamped input count
    assert "stock as-of" in page.text and "2026-07-15" in page.text
    assert "preset" in page.text and "policy.yaml" in page.text
    assert "inputs sha-stamped" in page.text
    # fingerprint table shows each input's sha16 from provenance.json
    prov = json.loads((root / rid / "out" / "provenance.json").read_text())
    one_sha = prov["files"]["soh"][0]["sha256"][:16]
    assert one_sha in page.text
    # per-section counts render as n/m; a green run hoists no failures
    assert re.search(r"—\s*\d+/\d+", page.text)
    assert "read these first" not in page.text
    assert "Expand all" in page.text


def test_discard_removes_stubs_and_refuses_executed_runs():
    env = _env()
    client, bundle, root = env["client"], env["bundle"], env["root"]
    rid = _new_run(client)                    # never-executed stub
    r = client.post(f"/runs/{rid}/discard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert not (root / rid).exists()
    # a finished run is a provenance record — the UI must refuse to delete it
    rid_a = _run_a(client, bundle)
    r = client.post(f"/runs/{rid_a}/discard", follow_redirects=False)
    assert r.headers["location"] == f"/runs/{rid_a}"
    assert (root / rid_a).exists()


def test_runs_list_badges_and_rail_navigation():
    env = _env()
    client, bundle = env["client"], env["bundle"]
    rid_a = _run_a(client, bundle)
    home = client.get("/")
    assert "policy.yaml" in home.text, "preset filename badges the run row"
    assert "2026-" in home.text or "202" in home.text  # created date visible
    assert "passed" in home.text, "checks summary badges finished runs"
    # the report's rail links back to visited steps and marks the current one
    page = client.get(f"/runs/{rid_a}")
    assert 'aria-current="step"' in page.text
    assert f'href="/runs/{rid_a}/configure"' in page.text
    # humanized role headings on the files page
    stub = _new_run(client)
    files_page = client.get(f"/runs/{stub}/inputs")
    assert "Stock on hand" in files_page.text
    assert "Store master" in files_page.text
    client.post(f"/runs/{stub}/discard")      # leave the list clean


def test_failed_run_surfaces_the_assertion_not_a_dead_server():
    env = _env()
    root = env["root"]
    d = root / "broken-run"
    (d / "out").mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "review"}))
    execute_run(str(d))          # no manifest.yaml: the child must fail SOFTLY
    st = read_status(d)
    assert st["state"] == "failed" and st["error"]
    page = env["client"].get("/runs/broken-run")
    assert page.status_code == 200 and "Run failed" in page.text


if __name__ == "__main__":
    import sys
    mod = sys.modules["__main__"]
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        getattr(mod, name)()
        print(f"{name}: OK")
