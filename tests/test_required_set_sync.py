"""Hermetic tests for .github/scripts/check-required-set.py (#6144).

The guard exists because the same required-status-check set is declared in
THREE places — live branch protection, `.mergify.yml`'s `queue_conditions` /
`merge_conditions`, and `.github/settings.yml` — and until now nothing read two
of them. `.mergify.yml` stated the invariant in a COMMENT ("each required check
named in EXACTLY ONE of the two lists") and carried a hand-maintained
"Last reconciled with the live required set: 2026-09-26" line. A comment cannot
fail.

Both drift directions are defects and they are not symmetric:

* a required name in NEITHER mergify list stops the file DESCRIBING reality;
* a name in `merge_conditions` that the queue branch never reports on
  DEADLOCKS the queue for every PR.

The third check is the one this repo has already paid for: `python-ci-gate`
observes its legs through TWO machine-readable structures — its `needs:` list
and the `LEGS` heredoc table that decides what a non-`success` result MEANS. A
leg in `needs:` with no `LEGS` row still trips rule 1 (which greps the joined
results for `failure|cancelled`), but a leg that reports `skipped` has NO row to
fail closed on — so the required check would CERTIFY a shard the selector
selected and GitHub never ran. That is #5219 (a green required check over a tree
whose shard did not run) reached through the other door, and nothing pinned it.

Hermetic: no network. The env seams (`MERGIFY_CONFIG`, `BRANCH_PROTECTION_DECLARATION`,
`WORKFLOWS_DIR`, `PYTHON_CI_WORKFLOW`) point the guard at fixtures. The `--live`
path shells out to `gh` and is exercised only when `REQUIRED_SET_SYNC_LIVE=1`.

Exit contract (fail-closed): 0 clean, 1 violation, 2 could-not-measure —
"nothing was compared" is never a pass.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-required-set.py"

# Ambient seams a developer might have exported — popped for full hermeticity.
_AMBIENT = ("MERGIFY_CONFIG", "BRANCH_PROTECTION_DECLARATION", "WORKFLOWS_DIR",
            "PYTHON_CI_WORKFLOW", "REQUIRED_SET_SYNC_LIVE")


@pytest.fixture(scope="module")
def guard():
    """Import the guard script as a module (it is not on the package path)."""
    spec = importlib.util.spec_from_file_location("check_required_set", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def tmp_guard_env(guard, monkeypatch):
    """Point every seam at a throwaway dir; return (dir, helper to write files)."""
    d = Path(tempfile.mkdtemp(prefix="required-set-"))
    monkeypatch.setattr(guard, "MERGIFY_PATH", d / ".mergify.yml")
    monkeypatch.setattr(guard, "SETTINGS_PATH", d / "settings.yml")
    monkeypatch.setattr(guard, "WORKFLOWS_DIR", d / "workflows")
    monkeypatch.setattr(guard, "PYTHON_CI_PATH", d / "python-ci.yml")
    (d / "workflows").mkdir()
    return d


def _run(env_extra: dict[str, str], args: list[str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for key in _AMBIENT:
        env.pop(key, None)
    env.update(env_extra)
    return subprocess.run([sys.executable, str(SCRIPT), *(args or [])],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT)


def _empty_declaration(guard) -> None:
    """Make every declared bucket empty, so a fixture's names are 'unaccounted'."""
    guard.REQUIRED_SET.clear()


# ── the real repo must pass (the durable pin) ──────────────────────────────


def test_the_real_repo_agrees_offline(guard):
    """`.mergify.yml`, the enumeration, the mirror and the gate's LEGS all agree.

    This is the pin: it fails the moment any one of the four drifts from the
    others, without needing credentials or network.
    """
    code, violations, _ = guard.run(live=False)
    assert code == 0, f"required-set drift on the live tree: {violations}"
    assert violations == []


def test_the_real_gate_observes_exactly_the_legs_its_table_names(guard):
    """`needs:` and the `LEGS` heredoc describe the SAME set in the real workflow."""
    needs, legs = guard.gate_legs()
    assert set(needs) == legs
    assert len(needs) == len(legs) == 10, (
        "python-ci-gate's claim changed shape — update the enumeration's "
        "coverage accounting for the new leg")
    for leg in ("test", "test-slow", "test-carve-out"):
        assert leg in legs, f"{leg} is a long leg and must stay observable (#5219)"


def test_the_real_enumeration_partitions_every_name(guard):
    """Exactly one bucket per name, and no name without a recorded guarantee."""
    queue, merge = guard.declared_lists()
    assert not (queue & merge)
    assert queue and merge
    for name, (where, why) in guard.REQUIRED_SET.items():
        assert where in ("queue", "merge"), f"{name}: unknown bucket {where!r}"
        assert why.strip(), f"{name}: coverage accounting is empty"
    assert "python-ci-gate" in merge, (
        "the aggregate must be the one merge condition — it is the check the "
        "queue branch actually reports on")


# ── COMPOSITION: run() must actually CALL each check ───────────────────────
#
# The checks are pinned in isolation above, which is not enough: deleting a
# check call from `run()` left all 30 tests green (measured by the reviewer).
# These drive `run()` end-to-end through the env seams so the WIRING is pinned
# too — a refactor that drops a check now reddens.


def _write_minimal_gate(guard, needs: list[str], legs: list[str]) -> None:
    """A consistent gate whose `needs:` and LEGS rows are given independently."""
    guard.PYTHON_CI_PATH.write_text(
        "jobs:\n  python-ci-gate:\n    needs: [" + ", ".join(needs) + "]\n"
        "    steps:\n      - run: |\n          done <<'LEGS'\n"
        + "".join(f"          {leg}|${{{{ needs.{leg}.result }}}}|-\n" for leg in legs)
        + "          LEGS\n")


def _write_minimal_mergify(guard, queue: list[str], merge: list[str]) -> None:
    guard.MERGIFY_PATH.write_text(
        "queue_rules:\n  - name: main\n    queue_conditions:\n"
        + "".join(f"      - check-success={n}\n" for n in queue)
        + "    merge_conditions:\n"
        + "".join(f"      - check-success={n}\n" for n in merge))


def _write_workflow(guard, name: str, trigger: str, jobs: list[str]) -> None:
    guard.WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    (guard.WORKFLOWS_DIR / name).write_text(
        f"on:\n  {trigger}:\njobs:\n"
        + "".join(f"  {j}:\n    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
                  for j in jobs))


def test_run_flags_a_legs_mismatch_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_gate_legs` (the #5219 door)."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["beta"])  # alpha has no row
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("alpha" in v and "skipped" in v for v in violations), violations


def test_run_flags_an_unproducible_queue_condition_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_deadlock` for the ENTRY bucket too."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "push.yml", "push", ["alpha"])  # alpha: push-only
    _write_workflow(guard, "pr.yml", "pull_request", ["beta"])
    _write_minimal_gate(guard, needs=["beta"], legs=["beta"])
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("ENTRY stalls" in v for v in violations), violations


def test_run_flags_a_stale_mirror_end_to_end(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_settings`."""
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - redis-guard\n")
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("stale declarative mirror" in v for v in violations), violations


# ── the mirror must speak for `main` ───────────────────────────────────────


def test_run_flags_a_mirror_that_also_speaks_for_another_branch(guard, tmp_guard_env, monkeypatch):
    """COMPOSITION: `run()` must call `check_settings_branches`.

    The `main` entry here is CORRECT and complete, so `check_settings` does NOT
    fire — only the off-main check can redden. That is what pins THIS call:
    without it this test would be satisfied by the other mirror check.
    """
    _write_minimal_mergify(guard, ["alpha"], ["beta"])
    _write_workflow(guard, "pr.yml", "pull_request", ["alpha", "beta"])
    _write_minimal_gate(guard, needs=["alpha", "beta"], legs=["alpha", "beta"])
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n"
        "    - branch: main\n      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - alpha\n          - beta\n"
        "    - branch: develop\n      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - ghost\n")
    monkeypatch.setattr(guard, "REQUIRED_SET",
                        {"alpha": ("queue", "x"), "beta": ("merge", "y")})
    code, violations, _ = guard.run()
    assert code == 1, violations
    assert any("rather than `main`" in v for v in violations), violations


def test_a_mirror_for_another_branch_is_a_violation(guard, tmp_guard_env):
    """.github/settings.yml is a mirror of `main`; contexts for `develop` are mis-filed."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - branch: develop\n"
        "      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - docs\n          - python-ci-gate\n")
    assert guard.declared_settings_contexts() == set()
    assert guard.declared_settings_off_main() == ["develop"]
    assert guard.check_settings_branches(["develop"]), "must be flagged"


def test_a_second_entry_for_another_branch_contributes_no_contexts(guard, tmp_guard_env):
    """The main entry is read; a sibling branch entry neither adds nor hides names."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n"
        "    - branch: main\n      required_status_checks:\n        strict: false\n"
        "        contexts:\n          - docs\n"
        "    - branch: develop\n      required_status_checks:\n"
        "        contexts:\n          - ghost\n")
    assert guard.declared_settings_contexts() == {"docs"}
    assert guard.declared_settings_off_main() == ["develop"]


def test_an_entry_without_a_branch_is_not_treated_as_main(guard, tmp_guard_env):
    """A missing `branch:` must not be silently accepted as `main`."""
    guard.SETTINGS_PATH.write_text(
        "repository:\n  branch-protection:\n    - required_status_checks:\n"
        "        contexts:\n          - docs\n")
    assert guard.declared_settings_contexts() == set()
    assert guard.declared_settings_off_main() == ["<missing branch>"]


def test_the_real_queue_lists_are_all_produced_on_pr_refs(guard):
    """Every condition the queue waits on must exist on a PR-like ref — BOTH lists.

    `queue_conditions` gate entry against the PR head and `merge_conditions`
    gate the merge against the queue branch; both refs are PR-like, so an
    unproducible name in EITHER stalls every PR.
    """
    parsed = guard.load_mergify()
    producible = guard.producible_on_pull_request()
    for bucket, names in parsed.items():
        assert not guard.check_deadlock(names, producible, f"{bucket}_conditions"), bucket


def test_the_real_declarative_mirror_is_not_stale(guard):
    """`.github/settings.yml` must agree with the enumeration (or not exist)."""
    queue, merge = guard.declared_lists()
    contexts = guard.declared_settings_contexts()
    assert guard.check_settings(contexts, queue | merge) == []


# ── partition mutations: a dropped or mis-filed name must fail ─────────────


def test_a_name_missing_from_the_enumeration_fails(guard):
    """A `check-success=` in `.mergify.yml` with no enumeration entry fails."""
    parsed = {"queue": {"docs"}, "merge": {"python-ci-gate", "smuggled-check"}}
    violations = guard.check_partition(parsed)
    assert any("smuggled-check" in v and "unaccounted" in v for v in violations), violations


def test_a_declared_name_dropped_from_mergify_fails(guard):
    """A declared name absent from `.mergify.yml` fails (a dropped condition)."""
    parsed = {"queue": {"docs"}, "merge": {"python-ci-gate"}}
    violations = guard.check_partition(parsed)
    assert any("missing from .mergify.yml" in v for v in violations), violations


def test_a_name_in_both_lists_fails(guard):
    """The invariant is EXACTLY ONE list — an overlap is a mis-filing."""
    parsed = {"queue": {"docs", "python-ci-gate"}, "merge": {"python-ci-gate"}}
    violations = guard.check_partition(parsed)
    assert any("BOTH" in v for v in violations), violations


def test_an_empty_guarantee_fails(guard, monkeypatch):
    """Coverage accounting cannot be blank — that is the #2656 failure shape."""
    monkeypatch.setitem(guard.REQUIRED_SET, "docs", ("queue", "   "))
    parsed = {"queue": {"docs"}, "merge": set()}
    violations = guard.check_partition(parsed)
    assert any("no recorded guarantee" in v for v in violations), violations


# ── the deadlock mutation ──────────────────────────────────────────────────


def test_a_merge_condition_only_a_push_workflow_produces_is_a_deadlock(guard, tmp_guard_env):
    """A push-only check is never reported on the queue branch → deadlock."""
    (tmp_guard_env / "workflows" / "push-only.yml").write_text(
        "on:\n  push:\n    branches: [main]\njobs:\n  push-only-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    producible = guard.producible_on_pull_request()
    assert "push-only-gate" not in producible
    violations = guard.check_deadlock({"push-only-gate"}, producible)
    assert any("DEADLOCK" in v for v in violations), violations


def test_a_queue_condition_only_a_push_workflow_produces_stalls_entry(guard, tmp_guard_env):
    """The ENTRY half: an unproducible queue_condition stalls entry forever."""
    (tmp_guard_env / "workflows" / "push-only-entry.yml").write_text(
        "on:\n  push:\n    branches: [main]\njobs:\n  entry-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    producible = guard.producible_on_pull_request()
    assert "entry-gate" not in producible
    violations = guard.check_deadlock({"entry-gate"}, producible, "queue_conditions")
    assert any("ENTRY stalls" in v for v in violations), violations


def test_a_pull_request_check_is_not_a_deadlock(guard, tmp_guard_env):
    """The same check on a pull_request trigger is fine."""
    (tmp_guard_env / "workflows" / "pr.yml").write_text(
        "on:\n  pull_request:\njobs:\n  pr-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    producible = guard.producible_on_pull_request()
    assert "pr-gate" in producible
    assert guard.check_deadlock({"pr-gate"}, producible) == []


def test_a_workflow_parse_uses_the_boolean_on_key(guard, tmp_guard_env):
    """PyYAML resolves a bare `on:` key to the BOOLEAN True — it must still read."""
    import yaml as _yaml
    doc = _yaml.safe_load("on:\n  pull_request:\njobs:\n  g:\n    runs-on: ubuntu-latest\n")
    assert True in doc and "on" not in doc, "PyYAML changed its `on:` handling"
    (tmp_guard_env / "workflows" / "b.yml").write_text(
        "on:\n  pull_request:\njobs:\n  boolean-on-gate:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n")
    assert "boolean-on-gate" in guard.producible_on_pull_request()


# ── the gate-legs mutation: the #5219 door ─────────────────────────────────


def test_a_leg_in_needs_with_no_legs_row_would_certify_a_skipped_shard(guard):
    """The mutation that matters: drop a LEGS row and the leg escapes fail-closed."""
    needs = ["changes", "test", "test-slow"]
    legs = {"changes", "test"}  # `test-slow` row deleted
    violations = guard.check_gate_legs(needs, legs)
    assert any("test-slow" in v and "skipped" in v for v in violations), violations


def test_a_legs_row_for_a_leg_not_in_needs_is_dead(guard):
    """The other direction: a row whose result can never be read."""
    violations = guard.check_gate_legs(["changes"], {"changes", "ghost-leg"})
    assert any("ghost-leg" in v and "dead" in v for v in violations), violations


def test_an_empty_needs_observes_nothing(guard):
    """An aggregate that needs nothing certifies nothing."""
    violations = guard.check_gate_legs([], set())
    assert any("observes nothing" in v for v in violations), violations


def test_a_multi_ref_job_name_pairs_each_key_with_its_own_values(guard):
    """Each `matrix.<key>` placeholder takes THAT key's value, not the leftmost.

    Substituting the first regex match instead of the named key's placeholder
    cross-pairs the values and invents check names that do not exist — which
    would read as a deadlock once a job name carries two matrix refs.
    """
    rendered = guard._render_matrix(
        "test (${{ matrix.b }}-${{ matrix.a }})", {"a": ["A1", "A2"], "b": ["B1", "B2"]})
    # A two-key matrix expands to the full CROSS PRODUCT (as GitHub does); the
    # defect this pins is PAIRING — before the fix, every value landed in the
    # leftmost slot, so b's values never appeared in b's position.
    assert rendered == {
        "test (B1-A1)", "test (B1-A2)", "test (B2-A1)", "test (B2-A2)"}, rendered
    assert all(name.startswith("test (B") for name in rendered), rendered


def test_a_repeated_ref_of_the_same_key_renders_every_occurrence(guard):
    """`${{ matrix.a }}` twice must fill BOTH slots with the same value.

    A first-match-only substitution leaves the second occurrence LITERAL, which
    is a check name that can never match a real check — a spurious deadlock —
    and it survived the cross-key test above.
    """
    rendered = guard._render_matrix(
        "test (${{ matrix.a }}-${{ matrix.a }})", {"a": ["A1", "A2"]})
    assert rendered == {"test (A1-A1)", "test (A2-A2)"}, rendered
    assert all("${{" not in name for name in rendered), rendered


def test_a_whitespace_varied_placeholder_still_renders(guard):
    """`${{matrix.a}}` (no spaces) is still a placeholder, not a literal."""
    assert guard._render_matrix("t (${{matrix.a}})", {"a": ["A1"]}) == {"t (A1)"}
    assert guard._render_matrix("t (${{  matrix.a  }})", {"a": ["A1"]}) == {"t (A1)"}


def test_a_single_ref_job_name_renders_every_value(guard):
    """The live shape: `test (${{ matrix.half }})` over half=[a,b]."""
    assert guard._render_matrix("test (${{ matrix.half }})", {"half": ["a", "b"]}) == {
        "test (a)", "test (b)"}


def test_gate_legs_parses_the_heredoc_not_a_bare_marker(guard, tmp_guard_env):
    """The opener is the tail of a command (`done <<'LEGS'`), not a bare token."""
    (tmp_guard_env / "python-ci.yml").write_text(
        "jobs:\n"
        "  python-ci-gate:\n"
        "    needs: [alpha, beta]\n"
        "    steps:\n"
        "      - run: |\n"
        "          while IFS='|' read -r leg result selected; do\n"
        "            echo \"$leg\"\n"
        "          done <<'LEGS'\n"
        "          alpha|${{ needs.alpha.result }}|-\n"
        "          beta|${{ needs.beta.result }}|${{ needs.changes.outputs.python }}\n"
        "          LEGS\n")
    needs, legs = guard.gate_legs()
    assert needs == ["alpha", "beta"] and legs == {"alpha", "beta"}


def test_a_gate_without_a_legs_table_cannot_be_measured(guard, tmp_guard_env):
    """No table → cannot measure (exit 2), never a silent pass."""
    (tmp_guard_env / "python-ci.yml").write_text(
        "jobs:\n  python-ci-gate:\n    needs: [alpha]\n    steps:\n      - run: echo hi\n")
    with pytest.raises(guard.CannotMeasure):
        guard.gate_legs()


# ── the mirror and the fail-closed paths ───────────────────────────────────


def test_a_stale_mirror_fails(guard, tmp_guard_env):
    """A third list that disagrees is the defect this guard was written for."""
    (tmp_guard_env / "settings.yml").write_text(
        "repository:\n  branch-protection:\n    - branch: main\n"
        "      required_status_checks:\n        strict: true\n"
        "        contexts:\n          - redis-guard\n")
    violations = guard.check_settings({"redis-guard"}, {"docs", "python-ci-gate"})
    assert any("stale declarative mirror" in v for v in violations), violations


def test_an_absent_mirror_is_not_a_violation(guard):
    """Deleting the inert mirror is a legitimate fix."""
    assert guard.check_settings(None, {"docs"}) == []


def test_a_strict_mismatch_fails(guard):
    """.github/settings.yml's `strict: true` is the origin of #4764's stale premise."""
    violations = guard.check_declared_strict(False, True)
    assert any("strict" in v and "#4764" in v for v in violations), violations
    assert guard.check_declared_strict(False, False) == []
    assert guard.check_declared_strict(None, True) == [], "unknown live is not a mismatch"


def test_mergify_with_no_conditions_cannot_be_measured(guard, tmp_guard_env):
    """"Nothing to compare" is not a pass — exit 2."""
    guard.MERGIFY_PATH.write_text("queue_rules:\n  - name: main\n    queue_conditions:\n      - base=main\n")
    with pytest.raises(guard.CannotMeasure):
        guard.load_mergify()


def test_missing_mergify_cannot_be_measured(guard, tmp_guard_env):
    with pytest.raises(guard.CannotMeasure):
        guard.load_mergify()


def test_the_cli_exits_two_when_it_cannot_measure():
    """End-to-end fail-closed: a nonexistent config must not exit 0."""
    d = Path(tempfile.mkdtemp(prefix="required-set-missing-"))
    r = _run({"MERGIFY_CONFIG": str(d / "absent.yml")})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "CANNOT MEASURE" in r.stdout


def test_the_cli_exits_zero_on_the_real_repo(guard, capsys):
    """`main()` maps a clean run to exit 0 (in-process: no extra interpreter)."""
    assert guard.main([]) == 0
    out = capsys.readouterr().out
    assert "required-set sync" in out and "python-ci-gate" in out
    assert "LEGS row" in out


def test_the_cli_exits_one_when_a_violation_is_found(guard, capsys, monkeypatch):
    """`main()` maps a violation to exit 1 and emits an ::error:: annotation.

    `monkeypatch` (not a bare assignment) so the module-global enumeration is
    restored — otherwise the mutated set leaks into every later test.
    """
    monkeypatch.setattr(
        guard, "REQUIRED_SET", {"ghost": ("merge", "a name nothing produces")})
    assert guard.main([]) == 1
    assert "::error::required-set drift" in capsys.readouterr().out


@pytest.mark.skipif(os.environ.get("REQUIRED_SET_SYNC_LIVE") != "1",
                    reason="needs admin-scoped gh credentials (set REQUIRED_SET_SYNC_LIVE=1)")
def test_live_branch_protection_matches_the_enumeration():
    """The `--live` path: live required contexts == the enumeration.

    Env-gated because it shells out to `gh` with admin scope. `--live` is the
    owner/rail check; the offline assertions above are the CI contract.
    """
    r = _run({}, args=["--live"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "LIVE required" in r.stdout and "LIVE strict" in r.stdout
