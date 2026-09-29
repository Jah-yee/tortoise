"""Guard suite for tools/pr_lead_time.py (#6139).

The tool shipped with NO tests, which is why the defects these pin went
unnoticed — every one of them was found by a reviewer reproducing it against
live data, not by a test. Each test below names the defect it prevents from
returning; the P1s were all reproduced on real PRs before being fixed:

* queue entry taken from a ZERO-LENGTH `Mergify Merge Queue` run — a queue
  EVALUATION probe, not residence. Measured: PR #6106 read review_wait 0.0 /
  queue_cycle 74.1 min against 63.0 / 11.2 min from the residence run (6.6x),
  and #5742 / #6079 have no residence run at all yet were assigned 47.7 and
  89.3 min of residence.
* a gate EARLIER than created_at, making seconds_a negative and aborting the
  WHOLE run through the partition invariant (PR #5137, a = -10762 s).
* an UNOBSERVED read exiting 0 — a page cap only warned on stderr, and a failed
  `gh` call raised an uncaught RuntimeError and exited 1 (the code reserved for
  "a gate would fail").
* a naive `--now` compared against an aware GitHub timestamp -> TypeError.
* a bot APPROVED review counted as human readiness, while `first_activity()`
  excludes bots.
* `--prune-after` zeroing the queue population so the report printed
  `queue_prs_closed_in_window=0` as though the queue were genuinely empty.

Everything here is pure: no network, no database, no subprocess. The `Gh` client
is not exercised.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import pr_lead_time as plt  # noqa: E402

T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017 — bare `python3` is 3.9
UTC = timezone.utc  # noqa: UP017 — same reason; one marker instead of four


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _mergify(start: datetime, end: datetime) -> dict:
    return {"name": "Mergify Merge Queue", "started_at": _iso(start),
            "completed_at": _iso(end)}


# --- queue entry: residence, not evaluation ---------------------------------

def test_zero_length_mergify_probe_is_not_queue_entry():
    """The P1. A zero-length run must not be read as the queue entry instant."""
    probe = _mergify(T0, T0)                       # zero-length evaluation
    residence = _mergify(T0 + timedelta(minutes=66), T0 + timedelta(minutes=77))
    entry = plt.queue_entry([probe, residence])
    assert entry == T0 + timedelta(minutes=66), (
        "the zero-length evaluation probe was taken as queue entry — this is the "
        "defect that overstated PR #6106's queue residence by 6.6x")
    assert entry != T0


def test_head_with_only_zero_length_probes_has_no_queue_marker():
    """A head whose only Mergify runs are probes must read as NO marker, so
    `merged_without_queue_marker` can fire instead of fabricating residence."""
    assert plt.queue_entry([_mergify(T0, T0), _mergify(T0, T0)]) is None


def test_queue_entry_picks_the_earliest_residence_run():
    a = _mergify(T0 + timedelta(hours=2), T0 + timedelta(hours=3))
    b = _mergify(T0 + timedelta(hours=1), T0 + timedelta(hours=4))
    assert plt.queue_entry([a, b]) == T0 + timedelta(hours=1)


def test_queue_entry_ignores_non_mergify_runs():
    other = {"name": "python-ci-gate", "started_at": _iso(T0),
             "completed_at": _iso(T0 + timedelta(hours=5))}
    assert plt.queue_entry([other]) is None


def test_queue_entry_is_none_without_mergify_runs():
    assert plt.queue_entry([]) is None


def test_queue_entry_ignores_an_unparseable_span():
    """A run missing one endpoint cannot establish residence."""
    assert plt.queue_entry([{"name": "Mergify Merge Queue", "started_at": _iso(T0)}]) is None


# --- gate clamp into the PR's own life --------------------------------------

def test_gate_before_creation_is_clamped_to_created():
    """The P1 reproduced as PR #5137: gate ~18:49Z, created 21:48:44Z."""
    created = T0
    merged = T0 + timedelta(hours=1)
    gate = T0 - timedelta(hours=3)                 # already green at PR creation
    assert plt.clamp_gate(gate, created, merged) == created


def test_clamped_gate_keeps_segments_non_negative():
    """The consequence the whole-run abort came from."""
    created, merged = T0, T0 + timedelta(hours=2)
    gate = plt.clamp_gate(T0 - timedelta(hours=3), created, merged)
    assert (gate - created).total_seconds() == 0
    assert (merged - gate).total_seconds() >= 0


def test_gate_after_merge_is_clamped_to_merged():
    created, merged = T0, T0 + timedelta(hours=1)
    assert plt.clamp_gate(T0 + timedelta(hours=5), created, merged) == merged


def test_gate_inside_the_life_is_unchanged():
    created, merged, gate = T0, T0 + timedelta(hours=2), T0 + timedelta(hours=1)
    assert plt.clamp_gate(gate, created, merged) == gate


def test_none_gate_passes_through():
    assert plt.clamp_gate(None, T0, T0 + timedelta(hours=1)) is None


# --- timestamps -------------------------------------------------------------

def test_naive_timestamp_is_read_as_utc():
    """A bare `--now` used to raise TypeError against an aware GitHub value."""
    parsed = plt.ts("2026-01-01T00:00:00")
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed == datetime(2026, 1, 1, tzinfo=UTC)
    # the comparison that used to explode
    assert parsed < datetime.now(UTC)


def test_zulu_timestamp_is_aware_and_equal():
    assert plt.ts("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)


def test_missing_timestamp_is_none():
    assert plt.ts(None) is None
    assert plt.ts("") is None


# --- exit-code discipline ---------------------------------------------------

def _run_main(monkeypatch, argv_extra=None, result=None, raises=None,
              truncates=None):
    def fake_measure(*_a, **_k):
        # The real caps record INTO _TRUNCATIONS while measure() sweeps, and
        # main() clears it on entry — so a seed cannot be set from outside and
        # must be produced from inside, exactly as Gh.paged does.
        if truncates:
            plt._TRUNCATIONS.extend(truncates)
        if raises is not None:
            raise raises
        return result
    monkeypatch.setattr(plt, "measure", fake_measure)
    argv = ["--repo-root", str(ROOT), "--days", "0"] + (argv_extra or [])
    try:
        return plt.main(argv)
    finally:
        plt._TRUNCATIONS.clear()


def test_main_exits_unknown_on_empty_population(monkeypatch, capsys):
    """An empty window used to return 0 with every `verified` flag true."""
    rc = _run_main(monkeypatch, result={"prs": []})
    assert rc == 2, "an EMPTY population must be UNKNOWN, never 0"


def test_main_exits_unknown_when_a_read_was_truncated(monkeypatch, capsys):
    """A page cap only wrote a stderr warning and still exited 0."""
    rc = _run_main(monkeypatch, result={"prs": [{"number": 1}]},
                   truncates=["closed-PR scan"])
    assert rc == 2, "a truncated population must be UNKNOWN, never 0"


def test_main_exits_unknown_on_a_failed_read(monkeypatch, capsys):
    """A failed `gh` call raised an uncaught RuntimeError -> exit 1."""
    rc = _run_main(monkeypatch, raises=RuntimeError("gh api failed: rate limit"))
    assert rc == 2, "an unobserved read must be UNKNOWN (2), not the verdict code 1"


def test_partition_violation_is_not_reported_as_unknown(monkeypatch):
    """`1` stays reserved for a real verdict: an invariant failure must NOT be
    swallowed into the UNKNOWN path."""
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, raises=SystemExit("PARTITION VIOLATED: legs != elapsed"))


# --- the fabricated zero ----------------------------------------------------

def test_prune_after_does_not_zero_the_queue_population():
    """`--prune-after` cleared `queue_prs`, so the report printed
    `queue_prs_closed_in_window=0` as if the queue were genuinely empty — the
    'single largest accounting error in earlier readings' this tool exists to
    prevent. Pinned against the source so it cannot quietly return."""
    src = (ROOT / "tools" / "pr_lead_time.py").read_text()
    # strip comments: the source legitimately MENTIONS the removed expression in
    # the comment that explains why it is gone — the pin is on executable code.
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "queue_prs[:0]" not in code, (
        "--prune-after must not empty the queue population; the report would "
        "read a fabricated 0 as a measured one")
