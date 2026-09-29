#!/usr/bin/env python3
"""Required-set sync guard (#6144): the required check list, the queue's lists,
and the declarative mirror must be ONE set — and every name in it must be
accounted for.

WHY THIS EXISTS
---------------
The same set of names is declared in three places, and until now **nothing
read two of them**:

  1. LIVE branch protection — `branches/main/protection` `.required_status_checks`.
     The source of truth. (The merge gate's `contexts`.)
  2. `.mergify.yml` `queue_rules[].queue_conditions` / `merge_conditions`.
     The merge queue's conditions. The file states the invariant in a COMMENT
     — *"each required check named in EXACTLY ONE of the two lists"* — and
     carries a human-maintained line: *"Last reconciled with the live required
     set: 2026-09-26"*. A comment cannot fail, and the date is only as good as
     its last editor.
  3. `.github/settings.yml` — a declarative mirror.

Both drift directions are defects, and they are NOT symmetric:

  * a required name in **NEITHER** mergify list is still enforced by Mergify's
    branch-protection injection, so the damage is that the file stops
    DESCRIBING reality (a maintenance trap, not an outage);
  * a name in **`merge_conditions`** that the queue branch never reports on
    **DEADLOCKS the queue for every PR** — the merge waits forever for a check
    that will never arrive;
  * a name in **`queue_conditions`** that no PR-triggered workflow produces is
    the same hazard at the ENTRY gate: the condition can never become true, so
    no PR can even enter the queue.

  Both buckets are evaluated against PR-like refs (the PR head for entry, the
  queue branch for the merge), so `check_deadlock` runs over BOTH.

WHY A GUARD AND NOT A TIDIER COMMENT
------------------------------------
This repo has already recorded the failure mode of changing the CI surface
without accounting for it: splitting the PR-tier surface **silently dropped the
#2656 manifest-drift gate plus eight others** (issue #6144, tortoise #2656).
The requirement is the one the issue itself writes down — the change is correct
only if *every guarantee is accounted for*, "an explicit enumeration with a
test, not a comment". This file is that enumeration; `tests/test_required_set_sync.py`
is that test.

WHAT IT DOES NOT DO
-------------------
It does not EDIT branch protection, and it does not propose a partition of the
test surface. Changing the required set is a branch-protection change and the
partition depends on #6139.

It READS live branch protection only under `--live`, which is a manual owner/rail
check and NOT part of CI: that read needs admin scope, which `GITHUB_TOKEN` does
not have. So the WIRED assertion is the offline one. A name added to or removed
from live protection ALONE — with no edit to `.mergify.yml` or the mirror — is
caught by `--live` only, and therefore by nobody automatically. Stated here so a
reader does not over-trust the CI step; the durable fix would be a scheduled job
authorised to read protection.

EXIT CODES
----------
0  every check passed
1  a violation (drift, mis-filing, unaccounted name, unproducible check)
2  could not measure (file missing/unparsable, nothing to compare, live read
   failed) — fail-closed: "nothing was compared" is never a pass.

   ONE NAMED EXCEPTION: an ABSENT `.github/settings.yml` is exit 0, not 2. The
   declarative mirror is OPTIONAL — a file that does not exist declares nothing
   to compare, and requiring it would red every repo that does not keep one.
   The moment the file EXISTS it must be usable: present-but-unparsable is 2,
   and present-but-declaring-nothing is a violation (1). "Missing" and "empty"
   are deliberately different verdicts; only the former is a skip.
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


@functools.lru_cache(maxsize=64)
def _read_yaml_cached(path_str: str, mtime_ns: int, size: int) -> Any:
    del mtime_ns, size  # cache-key material only
    try:
        text = Path(path_str).read_text()
    except (UnicodeDecodeError, OSError) as exc:
        # An undecodable/unreadable file is UNPARSABLE, so it must reach the
        # documented exit 2 with an annotation — not escape as a
        # UnicodeDecodeError traceback with exit 1 and no `::error::`.
        raise CannotMeasure(
            f"{path_str}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    doc = yaml.safe_load(text)
    # ONLY None (an empty document) becomes {}. The previous `or {}` coerced EVERY
    # falsy parse — so a top-level `[]`, `false` or `0` became `{}`, which then
    # reads as "the file declares nothing" and SKIPS its check. A fail-open whose
    # presence depended on the shape: `[a, b]` was caught, `[]` was not.
    return {} if doc is None else doc


def read_yaml(path: Path) -> Any:
    """Parse a YAML file, memoized on (path, mtime, size).

    `run()` parses `.github/workflows/python-ci.yml` (~2.2k lines) TWICE — once
    as one workflow among 26, once for the gate's `LEGS` table — and the tests
    call `run()` several times. Keyed on the file's identity, so a rewritten
    fixture is re-read rather than served stale. Callers must NOT mutate the
    result.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        # The stat is itself a filesystem read: a BROKEN SYMLINK matching the
        # workflows glob raises FileNotFoundError HERE, outside
        # `_read_yaml_cached`'s guard, and would escape as a traceback.
        raise CannotMeasure(
            f"{path}: unreadable ({type(exc).__name__}: {exc}) — cannot measure") from exc
    return _read_yaml_cached(str(path), stat.st_mtime_ns, stat.st_size)

REPO_ROOT = Path(__file__).resolve().parents[2]

MERGIFY_PATH = Path(os.environ.get("MERGIFY_CONFIG", REPO_ROOT / ".mergify.yml"))
SETTINGS_PATH = Path(
    os.environ.get("BRANCH_PROTECTION_DECLARATION", REPO_ROOT / ".github" / "settings.yml")
)
WORKFLOWS_DIR = Path(os.environ.get("WORKFLOWS_DIR", REPO_ROOT / ".github" / "workflows"))
PYTHON_CI_PATH = Path(
    os.environ.get("PYTHON_CI_WORKFLOW", REPO_ROOT / ".github" / "workflows" / "python-ci.yml")
)
GATE_JOB = "python-ci-gate"
GATE_LEGS_HEREDOC = "<<'LEGS'"

CHECK_SUCCESS_PREFIX = "check-success="

# ── THE ENUMERATION (design decision 2) ───────────────────────────────────────
# Every required status check, the ONE mergify list it belongs in, and the
# pre-merge guarantee it actually provides. Adding a name here without a
# matching `.mergify.yml` entry fails; adding one to `.mergify.yml` without an
# entry here fails. That is the whole anti-silent-drop property.
#
# `queue` = gated at ENTRY, against the PR head.
# `merge` = re-asserted at MERGE, against the queue branch — so the check MUST
#           be one the queue branch actually reports (see check_deadlock).
REQUIRED_SET: dict[str, tuple[str, str]] = {
    "pricing-artifact": (
        "queue",
        "the pricing artefact regenerates and matches the committed product/pricing.json",
    ),
    "docs": (
        "queue",
        "the docs index / link surface resolves",
    ),
    "test-isolation": (
        "queue",
        "tests do not leak state across each other (isolation contract)",
    ),
    "license-surface": (
        "queue",
        "the license surface is unmodified / correctly declared",
    ),
    "legal-e2e": (
        "queue",
        "the legal end-to-end surface still passes",
    ),
    "python-ci-gate": (
        "merge",
        "the whole Python CI aggregate: its `needs:` list is its entire claim "
        "(see the python-ci.yml block above the job and tests/test_ci_selection.py)",
    ),
}


class CannotMeasure(Exception):
    """Raised when a surface cannot be read — never silently treated as a pass."""


def require_mapping(value: Any, what: str) -> dict[str, Any]:
    """A parsed YAML value that must be a mapping. Unusable -> exit 2.

    Centralised because patching these one call site at a time kept leaving a
    sibling unguarded, and an unguarded `.get`/`.items()` surfaces as an
    AttributeError traceback with a NON-2 exit and no `::error::` annotation —
    which reads like a violation but names nothing.
    """
    if not isinstance(value, dict):
        raise CannotMeasure(
            f"{what} must be a mapping, got {type(value).__name__} — cannot measure")
    return value


def require_list(value: Any, what: str) -> list[Any]:
    """A parsed YAML value that must be a LIST. Unusable -> exit 2.

    A bare string is REFUSED rather than iterated: `contexts: docs` would
    otherwise silently become {'d','o','c','s'} — a wrong answer that looks fine.
    """
    if not isinstance(value, list):
        raise CannotMeasure(
            f"{what} must be a list, got {type(value).__name__} — cannot measure")
    return value


# ── parsing ───────────────────────────────────────────────────────────────────


def load_mergify(path: Path | None = None) -> dict[str, set[str]]:
    """Return {'queue': {...}, 'merge': {...}} from `.mergify.yml` check-success= conditions."""
    # Resolve at CALL time, not def time: a default argument would freeze the
    # env seam at import and ignore both the env and a monkeypatch.
    path = path or MERGIFY_PATH
    if not path.exists():
        raise CannotMeasure(f"mergify config not found: {path}")
    try:
        cfg = read_yaml(path)
    except yaml.YAMLError as exc:
        raise CannotMeasure(f"mergify config unparsable: {path}: {exc}") from exc
    if not isinstance(cfg, dict):
        # A top-level LIST/scalar is unusable, and an unguarded `.get` here would
        # surface as an AttributeError traceback with a NON-2 exit and no
        # `::error::` annotation — contradicting this module's exit contract.
        raise CannotMeasure(
            f"{path}: top level is not a mapping, got {type(cfg).__name__} — cannot measure")

    rules = cfg.get("queue_rules")
    if not isinstance(rules, list) or not rules:
        raise CannotMeasure(f"{path}: no queue_rules — nothing to compare is not a pass")

    out: dict[str, set[str]] = {"queue": set(), "merge": set()}
    for index, rule in enumerate(rules):
        require_mapping(rule, f"{path}: queue_rules[{index}]")
        for key, bucket in (("queue_conditions", "queue"), ("merge_conditions", "merge")):
            conds = rule.get(key)
            if conds is None:
                continue
            for cond in require_list(conds, f"{path}: queue_rules[{index}].{key}"):
                if isinstance(cond, str) and cond.startswith(CHECK_SUCCESS_PREFIX):
                    out[bucket].add(cond[len(CHECK_SUCCESS_PREFIX) :])

    if not out["queue"] and not out["merge"]:
        raise CannotMeasure(
            f"{path}: no {CHECK_SUCCESS_PREFIX}* conditions in any queue rule — "
            "nothing to compare is not a pass"
        )
    return out


def declared_lists() -> tuple[set[str], set[str]]:
    """The two declared buckets, from the enumeration above."""
    queue = {n for n, (where, _) in REQUIRED_SET.items() if where == "queue"}
    merge = {n for n, (where, _) in REQUIRED_SET.items() if where == "merge"}
    return queue, merge


def _triggers(workflow: dict[str, Any]) -> set[str]:
    """Workflow trigger names. PyYAML resolves a bare `on:` key to the BOOLEAN True."""
    raw = workflow.get("on", workflow.get(True))
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(x) for x in raw}
    if isinstance(raw, dict):
        return {str(k) for k in raw}
    return set()


_PR_IS_PUSH = re.compile(r"github\.event_name\s*==\s*['\"]push['\"]")
_PR_IS_NOT_PULL_REQUEST = re.compile(
    r"github\.event_name\s*(?:!=|<>)\s*['\"]pull_request(?:_target)?['\"]")
_PR_IS_PULL_REQUEST = re.compile(
    r"github\.event_name\s*==\s*['\"]pull_request(?:_target)?['\"]")


def _gated_off_pr_refs(job: dict[str, Any]) -> bool:
    """Can this job never report on a PR-like ref, because of its own `if:`?

    A workflow can be `on: pull_request` while a JOB inside it is gated to push
    only. Such a job is not a producible check on the PR head or the queue
    branch, so counting it as producible is a false negative in the deadlock
    check — exactly the hazard this guard exists to catch.

    The event-name COMPARISON is classified, not substring-matched: a brute
    `"pull_request" in condition` test also swallowed the NEGATED form
    (`!= 'pull_request'`), which is just as push-only as `== 'push'`.

    LIMIT, stated so it is not over-trusted: only these DECISIVE shapes are
    treated as unproducible. Anything else — including an `if:` we cannot
    classify — counts as producible, because the opposite default would turn an
    unrecognised expression into a false DEADLOCK on a healthy check, which is a
    worse failure than the false negative being closed.
    """
    condition = job.get("if")
    if not isinstance(condition, str):
        return False
    if _PR_IS_PULL_REQUEST.search(condition):
        return False  # names a PR event positively; it CAN run on a PR ref
    if _PR_IS_PUSH.search(condition):
        return True
    return bool(_PR_IS_NOT_PULL_REQUEST.search(condition))


_MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_]+)\s*\}\}")


def _render_matrix(template: str, matrix: dict[str, Any]) -> set[str]:
    """Render `test (${{ matrix.half }})` against the matrix values → {'test (a)', ...}.

    A template we cannot fully render is returned as-is (the caller then holds a
    name that will not match — fail-closed, not fail-open).
    """
    refs = set(_MATRIX_REF.findall(template))

    def expand(tmpl: str, keys: list[str], acc: set[str]) -> None:
        if not keys:
            acc.add(tmpl)
            return
        key = keys[0]
        values = matrix.get(key)
        if not isinstance(values, list):
            acc.add(tmpl)
            return
        # Substitute THIS key's placeholder, not the leftmost match: a template
        # with two different refs would otherwise pair an `a` value with a `b`
        # slot and yield names that do not exist.
        pattern = re.compile(r"\$\{\{\s*matrix\." + re.escape(key) + r"\s*\}\}")
        for value in values:
            # A CALLABLE replacement: passing `str(value)` directly makes the value
            # a replacement TEMPLATE, so a matrix value containing `\1` or
            # `\g<name>` raises re.error (exit 1, no annotation) and `\n`/`\t`
            # silently becomes a control character in the check name.
            expand(pattern.sub(lambda _m, v=value: str(v), tmpl), keys[1:], acc)

    acc: set[str] = set()
    expand(template, sorted(refs), acc)
    return acc


def producible_on_pull_request(workflows_dir: Path | None = None) -> set[str]:
    """Check names producible by any workflow that runs on a pull-request ref.

    The queue branch (`mergify/merge-queue/<sha>`) is a PR-like ref, so a check
    produced only by a `push:`-triggered workflow can never report there. Naming
    one in `merge_conditions` is the deadlock `.mergify.yml` warns about.
    """
    workflows_dir = workflows_dir or WORKFLOWS_DIR
    if not workflows_dir.is_dir():
        raise CannotMeasure(f"workflows dir not found: {workflows_dir}")
    names: set[str] = set()
    for path in sorted(workflows_dir.glob("*.y*ml")):
        try:
            workflow = read_yaml(path)
        except (yaml.YAMLError, CannotMeasure):
            # Deliberate: a broken workflow is other gates' business. Skipping it
            # can only make a NAME look unproducible, which reds the deadlock
            # check — a false RED. It can never manufacture a false GREEN, so this
            # stays a `continue` even for an unreadable file.
            continue
        if not isinstance(workflow, dict):
            continue
        if not (_triggers(workflow) & {"pull_request", "pull_request_target"}):
            continue
        jobs = workflow.get("jobs")
        require_mapping(jobs, f"{path}: `jobs:`")
        for job_id, job in jobs.items():
            require_mapping(job, f"{path}: jobs.{job_id}")
            if _gated_off_pr_refs(job):
                continue
            template = job.get("name")
            if isinstance(template, str) and template.strip():
                strategy = job.get("strategy")
                if strategy is None:
                    strategy = {}
                require_mapping(strategy, f"{path}: jobs.{job_id}.strategy")
                matrix = strategy.get("matrix")
                if matrix is None:
                    names.add(template.strip())
                else:
                    require_mapping(matrix, f"{path}: jobs.{job_id}.strategy.matrix")
                    names |= _render_matrix(template.strip(), matrix)
            else:
                names.add(str(job_id))
    return names


_KEY_ABSENT = object()


def _raw_settings_entries(path: Path | None) -> Any:
    """The `repository.branch-protection` VALUE, `_KEY_ABSENT` when there is NO FILE.

    `_KEY_ABSENT` means NO FILE — the mirror does not exist, so `check_settings`
    skips it. A file that exists but declares nothing returns `[]` (an empty
    declaration), which `check_settings` then flags. The distinction is
    load-bearing: an emptied declaration must reach `check_settings` as an EMPTY
    set, not as None — otherwise it passes vacuously, a fail-open in the one place
    this guard exists to fail closed.
    """
    path = path or SETTINGS_PATH
    if not os.path.lexists(path):
        # `os.path.lexists`, NOT `Path.exists()`: `exists()` also returns False for
        # a BROKEN SYMLINK, so a dangling `settings.yml` was classified "no file"
        # and the mirror check was skipped (exit 0) while the same shape at the
        # other two seams was fail-closed exit 2. Only a path with NO directory
        # entry at all is "the mirror does not exist"; a dangling link is a
        # present-but-unreadable entry, which `read_yaml`'s stat guard then reports.
        return _KEY_ABSENT
    try:
        doc = read_yaml(path)
    except yaml.YAMLError as exc:
        raise CannotMeasure(f"{path} unparsable: {exc}") from exc
    if not isinstance(doc, dict):
        # `[]`, `false`, `0`, a scalar — unusable input. `read_yaml` must NOT
        # coerce these to `{}`: doing so turned each into "declares nothing"
        # (exit 0) while the truthy version of the same shape exited 2.
        raise CannotMeasure(
            f"{path}: top level is not a mapping, got {type(doc).__name__} — cannot measure")
    repository = doc.get("repository")
    if repository is None:
        # Absent OR explicitly null: a present file declaring no branch protection.
        return []
    # PRESENT but not a mapping is UNUSABLE, not absent: returning _KEY_ABSENT
    # here would make `declared_settings_contexts` answer None, which
    # `check_settings` skips — silently disabling one of the three surfaces with
    # an exit 0.
    repository = require_mapping(repository, f"{path}: `repository`")
    if repository.get("branch-protection") is None:
        return []
    return repository["branch-protection"]


def _settings_entries(path: Path | None) -> list[dict[str, Any]]:
    """The `repository.branch-protection` entries; [] when the key is absent.

    A present key that is not a list of mappings is UNUSABLE, not empty: it exits
    2 rather than quietly yielding no entries (a dict here used to raise
    AttributeError, i.e. a non-zero exit — that must not silently become a pass).
    """
    raw = _raw_settings_entries(path)
    if raw is _KEY_ABSENT or raw is None:
        return []
    if not isinstance(raw, list):
        raise CannotMeasure(
            f"{path or SETTINGS_PATH}: repository.branch-protection must be a list, "
            f"got {type(raw).__name__} — cannot measure")
    for entry in raw:
        if not isinstance(entry, dict):
            raise CannotMeasure(
                f"{path or SETTINGS_PATH}: branch-protection entries must be mappings, "
                f"got {type(entry).__name__} — cannot measure")
    return raw


def _required_status_checks(entry: dict[str, Any]) -> dict[str, Any]:
    """`entry['required_status_checks']` as a mapping, or {} when absent.

    A present-but-non-mapping value is unusable and raises, rather than being
    silently treated as empty.
    """
    rsc = entry.get("required_status_checks")
    if rsc is None:
        return {}
    if not isinstance(rsc, dict):
        raise CannotMeasure(
            f"required_status_checks must be a mapping, got {type(rsc).__name__} — "
            "cannot measure")
    if rsc.get("contexts") is not None:
        require_list(rsc["contexts"], "required_status_checks.contexts")
    return rsc


def _is_main(entry: dict[str, Any]) -> bool:
    """Does this entry speak for `main`? A MISSING branch is not `main`."""
    return str(entry.get("branch", "")).strip() == "main"


def declared_settings_contexts(path: Path | None = None) -> set[str] | None:
    """Contexts declared for `main` by `.github/settings.yml`, or None if the mirror is absent.

    Note the file is INERT: probot-settings reads top-level `branches:`, while
    this file nests under `repository: -> branch-protection:`. That is precisely
    why it is dangerous — it reads as the branch-protection source of truth and
    is not. (The stale `strict: true` in it is the origin of #4764's premise.)

    Only `branch: main` entries count. Unioning every entry would let the mirror
    declare the RIGHT six contexts for the WRONG branch — the exact mis-filing
    this guard exists to catch — and still pass.

    Returns an EMPTY SET (not None) when the key is present but declares no main
    contexts, so `check_settings` flags it instead of skipping it.
    """
    if _raw_settings_entries(path) is _KEY_ABSENT:
        return None
    contexts: set[str] = set()
    for entry in _settings_entries(path):
        if not _is_main(entry):
            continue
        rsc = _required_status_checks(entry)
        contexts |= {str(c) for c in (rsc.get("contexts") or [])}
    return contexts


def declared_settings_off_main(path: Path | None = None) -> list[str]:
    """Branches other than `main` the mirror declares contexts or `strict` for."""
    off: list[str] = []
    for entry in _settings_entries(path):
        if _is_main(entry):
            continue
        rsc = _required_status_checks(entry)
        if rsc.get("contexts") or "strict" in rsc:
            off.append(str(entry.get("branch", "<missing branch>")))
    return off


def declared_settings_strict(path: Path | None = None) -> bool | None:
    """The mirror's `strict` flag for `main`, or None when it declares none."""
    for entry in _settings_entries(path):
        if not _is_main(entry):
            continue
        rsc = _required_status_checks(entry)
        if "strict" in rsc:
            value = rsc["strict"]
            # `strict: "false"` is a STRING and `bool("false")` is True — it
            # would manufacture a spurious #4764 mismatch against a live
            # `strict=false`. A non-boolean is unusable input, not a truthy one.
            if not isinstance(value, bool):
                raise CannotMeasure(
                    f"required_status_checks.strict must be a boolean, got "
                    f"{type(value).__name__} — cannot measure")
            return value
    return None


# ── checks ────────────────────────────────────────────────────────────────────


def check_partition(parsed: dict[str, set[str]]) -> list[str]:
    """INV-1/INV-2: declared == queue ∪ merge, each name in exactly one list."""
    problems: list[str] = []
    dq, dm = declared_lists()

    for bucket in ("queue", "merge"):
        declared, actual = (dq, parsed["queue"]) if bucket == "queue" else (dm, parsed["merge"])
        for name in sorted(actual - declared):
            problems.append(
                f"{name!r} is in .mergify.yml {bucket}_conditions but NOT in the enumeration "
                f"in {Path(__file__).name} — an unaccounted required check"
            )
        for name in sorted(declared - actual):
            problems.append(
                f"{name!r} is declared '{bucket}' but is missing from .mergify.yml "
                f"{bucket}_conditions — a dropped {bucket} condition"
            )

    if dq & dm:
        problems.append(
            f"{sorted(dq & dm)} declared in BOTH lists — the invariant is EXACTLY ONE"
        )
    if parsed["queue"] & parsed["merge"]:
        problems.append(
            f"{sorted(parsed['queue'] & parsed['merge'])} appear in BOTH "
            "queue_conditions and merge_conditions in .mergify.yml"
        )

    for name, (where, why) in REQUIRED_SET.items():
        if where not in ("queue", "merge"):
            problems.append(f"{name!r}: unknown bucket {where!r}")
        if not (why or "").strip():
            problems.append(f"{name!r}: no recorded guarantee — coverage accounting is empty")

    if not dq:
        problems.append("the enumeration declares an EMPTY queue bucket — fail-closed")
    if not dm:
        problems.append("the enumeration declares an EMPTY merge bucket — fail-closed")
    return problems


def check_deadlock(names: set[str], producible: set[str],
                   bucket: str = "merge_conditions") -> list[str]:
    """Every condition must be producible on a PR-like ref — in BOTH lists.

    `merge_conditions` gate the MERGE, evaluated against the queue branch; the
    queue branch is PR-like, so a check only a `push:`-triggered workflow
    produces can never report there and the merge waits forever.

    `queue_conditions` gate ENTRY, evaluated against the PR HEAD — also PR-like,
    so the same property is required. An unproducible entry condition means the
    condition can never become true and entry stalls for every PR. Checking only
    the merge list leaves that half unguarded.
    """
    consequence = {
        "merge_conditions": "the queue branch never reports it, so the queue DEADLOCKS for every PR",
        "queue_conditions": "so the condition can never become true and queue ENTRY stalls for every PR",
    }.get(bucket, "so the queue can never satisfy it")
    return [
        f"{name!r} is in {bucket} but NO pull_request-triggered workflow "
        f"produces it — {consequence}"
        for name in sorted(names - producible)
    ]


def gate_legs(path: Path | None = None) -> tuple[list[str], set[str]]:
    """Return (needs, LEGS rows) for the required aggregate job.

    Two machine-readable halves of ONE claim. `needs:` is the set of legs the
    gate observes; the `LEGS` heredoc is the fail-closed table that decides what
    a non-`success` result MEANS for each. They must describe the same set.
    """
    path = path or PYTHON_CI_PATH
    if not path.exists():
        raise CannotMeasure(f"workflow not found: {path}")
    try:
        workflow = read_yaml(path)
    except yaml.YAMLError as exc:
        raise CannotMeasure(f"{path} unparsable: {exc}") from exc
    if not isinstance(workflow, dict):
        raise CannotMeasure(
            f"{path}: top level is not a mapping, got {type(workflow).__name__} — cannot measure")
    jobs = workflow.get("jobs") or {}
    require_mapping(jobs, f"{path}: `jobs:`")
    if GATE_JOB not in jobs:
        raise CannotMeasure(f"{path}: no {GATE_JOB!r} job")
    gate = jobs[GATE_JOB] or {}
    require_mapping(gate, f"{path}: jobs.{GATE_JOB}")
    needs = gate.get("needs")
    if needs is None:
        needs = []
    elif isinstance(needs, str):
        if not needs.strip():
            # `needs: ''` is a present-but-blank job id: unusable, not "no needs".
            raise CannotMeasure(
                f"{path}: jobs.{GATE_JOB}.needs is a blank string — cannot measure")
        needs = [needs]
    elif not isinstance(needs, list):
        # `needs: 5` AND `needs: 0` / `false` / `''`: a truthiness-based `or []`
        # silently accepted the falsy non-lists as "no needs", so the same shape
        # exited 2 when truthy and 1 when falsy.
        raise CannotMeasure(
            f"{path}: jobs.{GATE_JOB}.needs must be a string or list, got "
            f"{type(needs).__name__} — cannot measure")

    for n in needs:
        # Elements, not just the container: `needs: [{a: b}]` reached `set(needs)`
        # and raised `TypeError: unhashable type: 'dict'` — exit 1, no annotation.
        if not isinstance(n, str) or not n.strip():
            raise CannotMeasure(
                f"{path}: jobs.{GATE_JOB}.needs entries must be non-blank strings, "
                f"got {type(n).__name__} — cannot measure")

    steps = gate.get("steps")
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        raise CannotMeasure(
            f"{path}: jobs.{GATE_JOB}.steps must be a list, got "
            f"{type(steps).__name__} — cannot measure")
    runs: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if run is None:
            continue
        if not isinstance(run, str):
            # `run: 5` / `true` / `[a, b]` — a `run or ""` only replaced FALSY
            # values, so a truthy non-string reached `.splitlines()` and raised
            # an AttributeError traceback (exit 1, no `::error::`).
            raise CannotMeasure(
                f"{path}: {GATE_JOB} step `run:` must be a string, got "
                f"{type(run).__name__} — cannot measure")
        runs.append(run)
    legs: set[str] = set()
    found_heredoc = False
    for run in runs:
        lines = run.splitlines()
        for idx, line in enumerate(lines):
            # The opener is the tail of a command: `done <<'LEGS'`, not a bare marker.
            if not line.strip().endswith(GATE_LEGS_HEREDOC):
                continue
            found_heredoc = True
            for row in lines[idx + 1 :]:
                if row.strip() == "LEGS":
                    break
                row = row.strip()
                if not row:
                    continue
                legs.add(row.split("|", 1)[0].strip())
    if not found_heredoc:
        raise CannotMeasure(
            f"{path}: {GATE_JOB} has no {GATE_LEGS_HEREDOC} table — the required "
            "check's per-leg verdict table could not be read; nothing is not a pass"
        )
    return needs, legs


def check_gate_legs(needs: list[str], legs: set[str]) -> list[str]:
    """`needs:` and the gate's LEGS table must describe the SAME set.

    A leg in `needs:` with no LEGS row still trips rule 1 (which greps the
    joined results for `failure|cancelled`), but a leg that reports `skipped`
    has NO row to fail closed on — so the required check would CERTIFY a shard
    the selector selected and GitHub never ran. That is the #5219 shape (a
    green required check over a tree whose shard did not run) reached through
    the other door, and nothing pinned it.
    """
    problems: list[str] = []
    as_set = set(needs)
    for leg in sorted(as_set - legs):
        problems.append(
            f"{leg!r} is in {GATE_JOB}.needs but has NO row in its LEGS table — a "
            "`skipped` result would not fail closed, so the required check could "
            "certify a shard that never ran"
        )
    for leg in sorted(legs - as_set):
        problems.append(
            f"{leg!r} has a LEGS row but is NOT in {GATE_JOB}.needs — the row is "
            "dead (its result can never be read), and the leg it names is "
            "unobservable by the required check"
        )
    if not as_set:
        problems.append(f"{GATE_JOB}.needs is EMPTY — the required check observes nothing")
    return problems


def check_settings_branches(off_main: list[str]) -> list[str]:
    """The mirror is a mirror of `main`; contexts for another branch are mis-filed."""
    if not off_main:
        return []
    return [
        f".github/settings.yml declares required_status_checks for branch(es) {off_main} "
        f"rather than `main` — a mirror of the wrong branch, which would silently "
        f"check nothing that matters"
    ]


def check_settings(contexts: set[str] | None, expected: set[str]) -> list[str]:
    """The declarative mirror must agree, or not exist."""
    if contexts is None:
        return []
    if contexts != expected:
        return [
            f".github/settings.yml declares contexts {sorted(contexts)} but the required set is "
            f"{sorted(expected)} — a stale declarative mirror. Update it or delete it; do not "
            f"leave a third list that reads as authoritative and is not."
        ]
    return []


def read_live_protection() -> tuple[set[str], bool | None]:
    """Live branch protection: (required contexts, strict). Needs admin-scoped credentials.

    `strict` is read because the declarative mirror also declares it, and a stale
    `strict: true` there is not cosmetic: it was the recorded premise of #4764.
    A null/absent value is returned as None (unknown), never coerced to False.
    """
    cmd = [
        "gh",
        "api",
        "repos/daniel-ospina/tortoise/branches/main/protection",
        "--jq",
        "{contexts: .required_status_checks.contexts, strict: .required_status_checks.strict}",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotMeasure(f"could not run gh: {exc}") from exc
    if out.returncode != 0:
        raise CannotMeasure(
            "could not read branch protection (needs admin-scoped credentials): "
            + (out.stderr or "").strip()[:300]
        )
    import json

    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise CannotMeasure(f"branch protection returned non-JSON: {out.stdout[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise CannotMeasure(
            f"branch protection payload must be an object, got {type(payload).__name__}")
    # Validate the payload's shape. A malformed `gh` response used to reach
    # `set(...)` and raise TypeError (exit 1, no annotation); the `--jq` normally
    # yields an array, so this is the last unguarded parsed-payload access.
    contexts = require_list(payload.get("contexts") or [], "branch protection contexts")
    for ctx in contexts:
        if not isinstance(ctx, str):
            raise CannotMeasure(
                f"branch protection contexts must be strings, got {type(ctx).__name__}"
                " — cannot measure")
    strict = payload.get("strict")
    if strict is not None and not isinstance(strict, bool):
        raise CannotMeasure(
            f"branch protection strict must be a boolean, got {type(strict).__name__}"
            " — cannot measure")
    return set(contexts), strict


def check_declared_strict(contexts_strict: bool | None, declared_strict: bool | None) -> list[str]:
    """The mirror's `strict` must match live — but only when both are known."""
    if contexts_strict is None or declared_strict is None:
        return []
    if contexts_strict != declared_strict:
        return [
            f".github/settings.yml declares strict={declared_strict} but live protection is "
            f"strict={contexts_strict} — a stale premise that has already produced a wrong "
            f"conclusion once (#4764)"
        ]
    return []


# ── entry point ───────────────────────────────────────────────────────────────


def run(live: bool = False) -> tuple[int, list[str], list[str]]:
    """Return (exit_code, violations, notes)."""
    notes: list[str] = []
    try:
        parsed = load_mergify()
        producible = producible_on_pull_request()
        settings = declared_settings_contexts()
        declared_strict = declared_settings_strict()
        off_main = declared_settings_off_main()
        needs, legs = gate_legs()
        if live:
            live_contexts, live_strict = read_live_protection()
        else:
            live_contexts, live_strict = None, None
    except CannotMeasure as exc:
        return 2, [], [f"CANNOT MEASURE: {exc}"]

    dq, dm = declared_lists()
    expected = dq | dm

    violations = (
        check_partition(parsed)
        + check_deadlock(parsed["queue"], producible, "queue_conditions")
        + check_deadlock(parsed["merge"], producible, "merge_conditions")
        + check_settings(settings, expected)
        + check_settings_branches(off_main)
        + check_declared_strict(live_strict, declared_strict)
        + check_gate_legs(needs, legs)
    )
    if live_contexts is not None:
        missing = sorted(expected - live_contexts)
        extra = sorted(live_contexts - expected)
        if missing:
            violations.append(
                f"required in the enumeration but NOT required on main: {missing} "
                f"— the enumeration has drifted ahead of branch protection"
            )
        if extra:
            violations.append(
                f"required on main but NOT in the enumeration: {extra} "
                f"— an unaccounted required check"
            )

    notes.append(f"queue_conditions : {sorted(parsed['queue'])}")
    notes.append(f"merge_conditions : {sorted(parsed['merge'])}")
    notes.append(f"declared total   : {len(expected)} name(s) "
                 f"(queue={len(dq)}, merge={len(dm)})")
    notes.append(f"producible on PR refs: {len(producible)} check name(s) across "
                 f"{len(list(WORKFLOWS_DIR.glob('*.y*ml')))} workflow file(s)")
    notes.append(f"{GATE_JOB}: {len(needs)} need(s), {len(legs)} LEGS row(s) — "
                 f"{'identical' if set(needs) == legs else 'MISMATCH'}")
    if live_contexts is not None:
        notes.append(f"LIVE required    : {sorted(live_contexts)}")
        notes.append(f"LIVE strict      : {live_strict}  | settings.yml: {declared_strict}")
    else:
        notes.append("LIVE required    : not read (offline mode; pass --live)")
    return (1 if violations else 0), violations, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--live",
        action="store_true",
        help="also compare against live branch protection (needs admin credentials)",
    )
    args = ap.parse_args(argv)

    code, violations, notes = run(live=args.live)
    for note in notes:
        print(f"  {note}")
    if code == 0:
        print("✅ required-set sync: the queue lists, the enumeration and the "
              "declarative mirror agree; every merge condition is producible on a PR ref")
        return 0
    if code == 2:
        for note in notes:
            if note.startswith("CANNOT MEASURE"):
                print(f"::error::{note}")
        return 2
    for violation in violations:
        print(f"::error::required-set drift: {violation}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
