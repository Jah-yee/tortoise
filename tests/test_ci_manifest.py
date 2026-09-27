"""Unit tests for the regenerable, value-validated selection manifest (#5050).

The substrate under test is `tools/ci_manifest.py`: the sweep that derives the
`durations` map from measured junit artifacts, the measurement record that is
its single source of truth, and the value / partition / guard-reachability
checks that `tools/ci_selection.py --integrity` delegates to.

Every assertion here pins a defect class from the #5050 root, not an
implementation detail:

* a wrong value, a missing row, a dead key           -> value_issues
* a classified file in no leg / two legs             -> partition_issues (#4835)
* a guard whose file selects no surface              -> guard_reachability (#3362/#4115/#4658)
* a newly registered file with no measurement        -> register_provisional (#4348/#4364)
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_manifest as cm  # noqa: E402
from tools import ci_selection as cs  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────


def _manifest() -> dict:
    return {
        "version": 1,
        "surfaces": {
            "core": ["test_a.py", "test_slow.py", "test_carve.py",
                     "test_both.py"],
            "other": ["test_b.py"],
        },
        "tier1": ["test_a.py"],
        "slow_files": ["test_slow.py", "test_both.py"],
        "carve_out": ["test_carve.py", "test_both.py"],
        "push_extra": [],
        "durations": {},
    }


def _junit(directory: Path, artifact: str, entries) -> Path:
    d = directory / artifact
    d.mkdir(parents=True, exist_ok=True)
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for file_, time in entries:
        ET.SubElement(suite, "testcase", file=file_, time=str(time))
    path = d / "junit.xml"
    path.write_text(ET.tostring(root, encoding="unicode"))
    return path


def _source(*, a=(), b=(), slow=(), carve=()) -> dict:
    return {
        "fast": {cm._bare(f): t for f, t in (*a, *b)},
        "slow": {cm._bare(f): t for f, t in slow},
        "carve_out": {cm._bare(f): t for f, t in carve},
    }


def _record_and_manifest():
    """A consistent (manifest, record) pair from one synthetic sweep."""
    m = _manifest()
    source = _source(
        a=[("tests/test_a.py", 1.23)],
        b=[("tests/test_b.py", 0.5)],
        slow=[("tests/test_slow.py", 10.0)],
        carve=[("tests/test_carve.py", 20.0), ("tests/test_both.py", 5.0)],
    )
    record = cm.sweep([("r1", source)], m, cm.seed_from_manifest(m))
    m["durations"] = {n: row["value"] for n, row in record["rows"].items()}
    return m, record


# ── the sweep: carrying-leg rule, max-across-runs, floors ─────────────────


def test_parse_junit_sums_per_file():
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    ET.SubElement(suite, "testcase", file="tests/test_x.py", time="1.5")
    ET.SubElement(suite, "testcase", file="tests/test_x.py", time="2.5")
    ET.SubElement(suite, "testcase", file="tests/sub/test_y.py", time="0.25")
    tmp = Path(pytest.importorskip("tempfile").mkdtemp())
    path = tmp / "junit.xml"
    path.write_text(ET.tostring(root, encoding="unicode"))
    assert cm.parse_junit(path) == {"test_x.py": 4.0, "sub/test_y.py": 0.25}


def test_carrying_leg_prefers_carve_out_over_slow():
    # test_both.py is a dual slow+carve file; it RUNS in the carve-out job, so
    # its weight must come from the carve-out artifact, not the slow legs.
    m = _manifest()
    fast = set(cs.fast_pool(m))
    slow = set(m["slow_files"])
    carve = cs.carve_out_files(m)
    assert cm.carrying_leg("test_both.py", fast, slow, carve) == "carve_out"
    assert cm.carrying_leg("test_a.py", fast, slow, carve) == "fast"
    assert cm.carrying_leg("test_slow.py", fast, slow, carve) == "slow"
    assert cm.carrying_leg("test_carve.py", fast, slow, carve) == "carve_out"


def test_sweep_takes_the_larger_across_runs_and_rounds():
    m = _manifest()
    r1 = _source(a=[("tests/test_a.py", 1.23)], b=[("tests/test_b.py", 0.04)])
    r2 = _source(a=[("tests/test_a.py", 2.04)], b=[("tests/test_b.py", 0.0)])
    record = cm.sweep([("r1", r1), ("r2", r2)], m, cm.seed_from_manifest(m))
    # larger across runs, then 1 dp; the 0.04/0.0 pair floors at 0.1.
    assert record["rows"]["test_a.py"]["value"] == 2.0
    assert record["rows"]["test_a.py"]["samples"] == {"r1": 1.23, "r2": 2.04}
    assert record["rows"]["test_b.py"]["value"] == 0.1


def test_sweep_records_unmeasured_rows_explicitly_and_carries_forward():
    m = _manifest()
    m["durations"] = {"test_a.py": 7.5}
    source = _source(b=[("tests/test_b.py", 1.0)])
    record = cm.sweep([("r1", source)], m, cm.seed_from_manifest(m))
    # test_a was never measured in this run: its committed value is carried
    # forward and MARKED, never silently defaulted to PROVISIONAL_WEIGHT.
    assert record["rows"]["test_a.py"]["unmeasured"] is True
    assert record["rows"]["test_a.py"]["value"] == 7.5
    # a file with no prior value gets the explicit provisional weight.
    m2 = _manifest()
    record2 = cm.sweep([("r1", source)], m2, cm.seed_from_manifest(m2))
    assert record2["rows"]["test_a.py"]["unmeasured"] is True
    assert record2["rows"]["test_a.py"]["value"] == cm.PROVISIONAL_WEIGHT


def test_sweep_retains_a_declared_row_even_when_measured(monkeypatch):
    # The #4766 RETAINED rule: a row no artifact can re-derive keeps its value.
    monkeypatch.setitem(cm.RETAINED, "test_a.py", "retained for the test")
    m = _manifest()
    m["durations"] = {"test_a.py": 28.1}
    record = cm.sweep([("r1", _source(a=[("tests/test_a.py", 3.1)]))], m,
                      cm.seed_from_manifest(m))
    row = record["rows"]["test_a.py"]
    assert row["value"] == 28.1 and row["retained"] is True
    assert row["unmeasured"] is True


def test_sweep_honours_the_pinned_equality(monkeypatch):
    monkeypatch.setitem(cm.PINS, "test_b.py", "test_a.py")
    m = _manifest()
    record = cm.sweep([("r1", _source(a=[("tests/test_a.py", 9.9)],
                                      b=[("tests/test_b.py", 0.2)]))],
                      m, cm.seed_from_manifest(m))
    assert record["rows"]["test_b.py"]["value"] == record["rows"]["test_a.py"]["value"]
    assert record["rows"]["test_b.py"]["pinned_to"] == "test_a.py"


def test_sweep_drops_a_dead_carve_only_row():
    # #4783: a carve-out-only key is dead (the carve-out job does not consume
    # the map) and must not survive into the record.
    m = _manifest()
    m["durations"] = {"test_carve.py": 20.0, "test_a.py": 1.0}
    record = cm.sweep([("r1", _source(carve=[("tests/test_carve.py", 20.0)]))],
                      m, cm.seed_from_manifest(m))
    assert "test_carve.py" not in record["rows"]
    assert "test_a.py" in record["rows"]


# ── rendering / rewriting the manifest in place ───────────────────────────


def test_rewrite_preserves_the_prose_header_and_trailing_keys(tmp_path):
    path = tmp_path / "ci-surfaces.yml"
    path.write_text(
        "surfaces:\n  core:\n    - test_a.py\n"
        "durations:\n"
        "# decision-carrying header the generator does NOT own\n"
        "# second header line\n"
        "  stale.py: 99.0\n"
        "guard_inputs:\n  core:\n    - config/pipelines.yaml\n")
    record = {"rows": {
        "test_a.py": {"leg": "fast", "samples": {"r": 1.0}, "value": 1.0},
        "test_b.py": {"leg": "fast", "samples": {}, "value": 2.0,
                      "unmeasured": True, "note": "carried forward"},
    }}
    cm.rewrite_manifest(path, record)
    text = path.read_text()
    assert "# decision-carrying header" in text
    assert text.index("durations:") < text.index("guard_inputs:")
    assert "stale.py" not in text
    assert "  test_b.py: 2  # unmeasured — carried forward" in text
    # the rendered order is by weight, descending (within the rows block).
    tail = text[text.index("durations:"):]
    assert tail.index("test_b.py") < tail.index("test_a.py")


# ── value validation ─────────────────────────────────────────────────────


def test_consistent_manifest_and_record_are_clean():
    m, record = _record_and_manifest()
    assert cm.value_issues(m, record) == []
    assert cm.value_issues(m, record) == []  # deterministic


@pytest.mark.parametrize("bad", [0.1, 1880.0])
def test_value_mismatch_fails(bad):
    m, record = _record_and_manifest()
    m["durations"]["test_a.py"] = bad
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i and "measurement record" in i for i in issues), issues


def test_missing_row_fails():
    m, record = _record_and_manifest()
    del m["durations"]["test_b.py"]
    issues = cm.value_issues(m, record)
    assert any("test_b.py" in i and "no durations row" in i for i in issues), issues


def test_carve_out_key_fails():
    m, record = _record_and_manifest()
    m["durations"]["test_carve.py"] = 20.0
    issues = cm.value_issues(m, record)
    assert any("test_carve.py" in i and "carve-out" in i for i in issues), issues


def test_key_with_no_record_row_fails():
    m, record = _record_and_manifest()
    del record["rows"]["test_a.py"]
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i and "no row in the measurement record" in i
               for i in issues), issues


def test_stale_unmeasured_marker_fails():
    m, record = _record_and_manifest()
    record["rows"]["test_a.py"]["unmeasured"] = True
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i and "stale marker" in i for i in issues), issues


def test_absent_record_fails(tmp_path, monkeypatch):
    m, _ = _record_and_manifest()
    monkeypatch.setattr(cm, "RECORD", tmp_path / "missing.json")
    issues = cm.value_issues(m, None)
    assert any("durations-source.json" in i for i in issues), issues


def test_a_huge_int_does_not_crash_the_value_check():
    # #3407 totality: a value beyond float range must be NAMED, not raise
    # OverflowError inside the gate that exists to report it.
    m, record = _record_and_manifest()
    m["durations"]["test_a.py"] = 10 ** 400
    issues = cm.value_issues(m, record)
    assert any("test_a.py" in i for i in issues), issues


# ── the partition invariant (+ #4835) ────────────────────────────────────


def test_partition_is_clean_on_the_committed_manifest():
    assert cm.partition_issues(cs.load_manifest()) == []


def test_partition_flags_a_file_dropped_from_every_leg(monkeypatch):
    m = cs.load_manifest()
    real = cs.push_legs(m)
    broken = {"half_a": real["half_a"][1:], "half_b": real["half_b"],
              "slow": real["slow"], "env_broken": real["env_broken"],
              "carve_out": real["carve_out"]}
    monkeypatch.setattr(cs, "push_legs", lambda manifest: broken)
    issues = cm.partition_issues(m)
    assert any("NO push leg" in i for i in issues), issues


def test_partition_flags_a_file_in_two_legs(monkeypatch):
    m = cs.load_manifest()
    real = cs.push_legs(m)
    broken = dict(real)
    broken["half_b"] = real["half_b"] + real["half_a"][:1]
    monkeypatch.setattr(cs, "push_legs", lambda manifest: broken)
    issues = cm.partition_issues(m)
    assert any("more than one leg" in i for i in issues), issues


def test_env_broken_file_is_not_reported_as_a_coverage_hole():
    # #4835: fast_files_absent_from_halves disagreed with fast_pool about the
    # env-broken set, reporting a permanent FALSE hole for test_agent_signup.py.
    m = cs.load_manifest()
    legs = cs.push_legs(m)
    halves = {"a": set(legs["half_a"]), "b": set(legs["half_b"])}
    absent = cs.fast_files_absent_from_halves(m, halves)
    assert "test_agent_signup.py" not in absent, absent
    assert absent == [], absent


# ── guard reachability (#3362/#4115/#4186/#4658) ─────────────────────────


def test_guard_reachability_is_clean_on_the_committed_tree():
    assert cm.guard_reachability_issues(cs.load_manifest()) == []


def test_guard_reachability_flags_a_dead_source_pattern(monkeypatch):
    m = cs.load_manifest()
    monkeypatch.setitem(
        cs.SOURCE_PATTERNS, "onboarding",
        tuple(cs.SOURCE_PATTERNS["onboarding"])
        + ("tools/__does_not_exist_5050__.py",))
    issues = cm.guard_reachability_issues(m)
    assert any("__does_not_exist_5050__" in i for i in issues), issues


def test_guard_reachability_flags_a_misdeclared_guard_input():
    # website/license.html selects `onboarding`; declaring it under `core` means
    # a PR editing the guarded page would not run the guard that reads it.
    m = dict(cs.load_manifest())
    m["guard_inputs"] = {"core": ["website/license.html"]}
    issues = cm.guard_reachability_issues(m)
    assert any("website/license.html" in i for i in issues), issues


def test_guard_reachability_flags_an_unselectable_tool_guard(monkeypatch):
    # The guard stays REGISTERED; the derivation that makes its tool selectable
    # is what is removed. This is the #3362/#4115 state on a tree before the
    # rule existed: `tests/test_surface_manifest.py` classified under `core`,
    # `tools/surface_manifest.py` selecting nothing.
    m = cs.load_manifest()
    monkeypatch.setattr(cs, "_tool_guard_surface", lambda path, manifest: [])
    issues = cm.guard_reachability_issues(m)
    assert any("tools/surface_manifest.py" in i for i in issues), issues


def test_a_tool_with_a_registered_guard_is_selectable():
    # The derived rule (ci_selection._tool_guard_surface) is what makes the
    # check above pass on the committed tree.
    r = cs.select(["tools/surface_manifest.py"], "pull_request",
                  cs.load_manifest())
    assert r["surfaces"] == ["core"], r
    assert "test_surface_manifest.py" in r["test_files"], r


def test_the_two_arch_docs_select_the_onboarding_guard():
    # #4658: a docs-only PR editing a guarded doc must run the guard.
    m = cs.load_manifest()
    for doc in ("docs/auth-architecture.md", "website/website_architecture.md"):
        r = cs.select([doc], "pull_request", m)
        assert "onboarding" in r["surfaces"], (doc, r)
        assert "test_no_legacy_token_path.py" in r["test_files"], (doc, r)


# ── bootstrap: atomic registration (#4348/#4364/#4817) ───────────────────


def _write_scratch(tmp_path, manifest, record):
    mpath = tmp_path / "ci-surfaces.yml"
    rpath = tmp_path / "record.json"
    body = {k: v for k, v in manifest.items() if k != "durations"}
    mpath.write_text(yaml.safe_dump(body) + "durations:\n# header\n")
    cm.write_record(record, rpath)
    return mpath, rpath


def test_register_provisional_keeps_the_contract_green(tmp_path):
    m, record = _record_and_manifest()
    m["surfaces"]["core"].append("test_new.py")
    mpath, rpath = _write_scratch(tmp_path, m, record)
    added = cm.register_provisional(["test_new.py"], mpath, rpath)
    assert added == ["test_new.py"]
    loaded_m = yaml.safe_load(mpath.read_text())
    loaded_record = cm.load_record(rpath)
    assert loaded_record["rows"]["test_new.py"]["unmeasured"] is True
    assert loaded_record["rows"]["test_new.py"]["value"] == cm.PROVISIONAL_WEIGHT
    assert loaded_m["durations"]["test_new.py"] == cm.PROVISIONAL_WEIGHT
    # the bootstrap trap is closed: a brand-new file with no measurement no
    # longer reds the strict presence check.
    assert cm.value_issues(loaded_m, loaded_record) == []


def test_register_provisional_is_idempotent(tmp_path):
    m, record = _record_and_manifest()
    m["surfaces"]["core"].append("test_new.py")
    mpath, rpath = _write_scratch(tmp_path, m, record)
    cm.register_provisional(["test_new.py"], mpath, rpath)
    assert cm.register_provisional(["test_new.py"], mpath, rpath) == []


# ── end-to-end CLI: sweep then check ─────────────────────────────────────


def test_cli_sweep_then_check_round_trip(tmp_path):
    m = _manifest()
    mpath, rpath = _write_scratch(tmp_path, m, {"rows": {}})
    junit = tmp_path / "run"
    _junit(junit, "pytest-log-test-a", [("tests/test_a.py", 4.04)])
    _junit(junit, "pytest-log-test-b", [("tests/test_b.py", 1.5)])
    _junit(junit, "pytest-log-test-slow", [("tests/test_slow.py", 12.0)])
    _junit(junit, "pytest-log-test-carve-out",
           [("tests/test_carve.py", 20.0), ("tests/test_both.py", 5.0)])
    assert cm.main(["sweep", "--junit-dir", str(junit), "--manifest", str(mpath),
                    "--record", str(rpath), "--write"]) == 0
    assert cm.main(["check", "--manifest", str(mpath), "--record", str(rpath)]) == 0
    # a hand-edit is caught by the same check.
    text = mpath.read_text().replace("test_a.py: 4", "test_a.py: 1880")
    mpath.write_text(text)
    assert cm.main(["check", "--manifest", str(mpath), "--record", str(rpath)]) == 1
