"""The PR lead-time decomposition must not silently drop a population (#6139).

`tools/pr_lead_time.py` answers a keystone question for epic #5215 — is CI
duration the binding constraint, or is the wait elsewhere? — and its entire
risk surface is **accounting**, not arithmetic: every way it can be wrong fails
*open*, by shrinking a leg or a bucket, and a shrunk leg still looks like a
number.

So this file does not re-derive the statistics. It pins the five specific
accounting defects that four verification rounds actually produced on the real
data, each of which was a wrong answer rather than a wrong line of code:

  1. a bucket set that did not sum to the population (12 merged PRs fell in no
     bucket, because their head never fully greened);
  2. a share computed over a *partial* denominator (`a2 + b`, excluding `a1`),
     which reported "90.5% of the mass" for a leg holding 31.2% of it;
  3. "dominant leg" left undefined, when mean and median disagreed;
  4. a context classified as red without applying the repo's own polarity
     (`cancelled`/`stale` are NOT red), which can mint false gate-integrity
     alarms from a cancelled run;
  5. `D` and `E` conflated, so "merged before its gate finished" and "merged
     with a gate that never succeeded" — different findings — reported as one.

Every expected value here is a **literal**, and the legs are constructed from
plain timestamps, so nothing is asserted against the implementation's own
opinion of itself.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pr_lead_time as plt  # noqa: E402, RUF100

UTC = UTC
REQUIRED = ["docs", "python-ci-gate"]
T0 = datetime(2026, 9, 21, tzinfo=UTC)


def at(days=0, hours=0, minutes=0) -> datetime:
    return T0 + timedelta(days=days, hours=hours, minutes=minutes)


# --- polarity (defect 4) ---------------------------------------------------

def test_a_success_is_green():
    assert plt.classify_contexts({"docs": ["success"]}) == {"docs": plt.GREEN}


def test_cancelled_and_stale_are_not_red():
    """The rail treats cancelled/stale as non-red; classifying them red would
    manufacture gate-integrity alarms out of runs that never failed."""
    assert plt.classify_contexts({"docs": ["cancelled"], "python-ci-gate": ["stale"]}) == {
        "docs": plt.NON_RED_NOT_GREEN,
        "python-ci-gate": plt.NON_RED_NOT_GREEN,
    }


def test_a_real_failure_is_red():
    assert plt.classify_contexts({"docs": ["failure"]}) == {"docs": plt.RED}


def test_an_unrecognised_conclusion_is_red():
    """Polarity, not a list: anything not known-non-red counts as red, so a new
    GitHub conclusion cannot slip through as a pass."""
    assert plt.classify_contexts({"docs": ["some_future_conclusion"]}) == {"docs": plt.RED}
    assert plt.classify_contexts({"docs": ["null"]}) == {"docs": plt.RED}


def test_success_wins_over_a_later_failure_attempt():
    """A re-run ADDS an attempt; the context has succeeded, so it is green."""
    assert plt.classify_contexts({"docs": ["failure", "success"]}) == {"docs": plt.GREEN}


# --- head state ------------------------------------------------------------

def test_absent_context_is_reported_separately_from_a_red_one():
    st = plt.head_state(REQUIRED, {"docs": ["failure"]})
    assert st["never_ran"] == ["python-ci-gate"]
    assert st["red"] == ["docs"]
    assert st["greened"] is False


def test_greened_requires_every_required_context():
    assert plt.head_state(REQUIRED, {"docs": ["success"]})["greened"] is False
    assert plt.head_state(REQUIRED, {"docs": ["success"], "python-ci-gate": ["success"]})["greened"] is True


def test_non_red_but_not_green_does_not_green_the_gate():
    st = plt.head_state(REQUIRED, {"docs": ["success"], "python-ci-gate": ["cancelled"]})
    assert st["greened"] is False
    assert st["non_red_not_green"] == ["python-ci-gate"]


# --- buckets: D and E are different findings (defects 1 and 5) --------------

def test_a_head_that_never_greened_is_d_not_e():
    """Both D and E were merged before their gate finished. E requires the gate
    to have LATER greened, so this must be D."""
    bucket, legs = plt.decompose(at(0), at(hours=1), at(minutes=5), None)
    assert (bucket, legs) == ("D", None)


def test_merged_before_its_gate_green_is_e():
    bucket, legs = plt.decompose(at(0), at(hours=1), at(minutes=5), at(hours=2))
    assert (bucket, legs) == ("E", None)


def test_a_normal_pr_yields_three_consecutive_legs():
    created, first, green, merged = at(0), at(hours=2), at(hours=3), at(hours=5)
    bucket, legs = plt.decompose(created, merged, first, green)
    assert bucket == "A1A2B"
    assert legs == {"a1": 2 * 3600, "a2": 3600, "b": 2 * 3600}
    # the identity the write-up uses: the legs reconstruct the life
    assert sum(legs.values()) == (merged - created).total_seconds()


def test_a_leg_cannot_be_negative():
    """These guards are live: a merge earlier than its own gate green is E, but
    a FIRST CHECK before creation is a bad timestamp and must not pass silently.
    An explicit raise, not `assert` — an assert would vanish under `python -O`."""
    with pytest.raises(ValueError):
        plt.decompose(at(days=1), at(days=1, hours=2), at(0), at(days=1, hours=1))


def test_no_first_check_does_not_get_invented():
    """A merged head with no recorded check-run start is UNKNOWN, never a zero leg."""
    bucket, legs = plt.decompose(at(0), at(hours=1), None, at(hours=1))
    assert (bucket, legs) == ("UNKNOWN", None)


# --- shares: the FULL life is the denominator (defect 2) -------------------

def test_shares_use_the_full_life_not_a_partial_denominator():
    """The defect that produced '90.5% of the mass'. a1 dominates here, so a
    denominator of (a2 + b) would give b ~100% and hide the real leader."""
    legs = {"a1": [10 * 3600.0], "a2": [1 * 3600.0], "b": [2 * 3600.0]}
    sh = plt.shares(legs)
    assert set(sh["by_sum"]) == {"a1", "a2", "b"}
    assert sh["by_sum"]["a1"] == pytest.approx(10 / 13)
    assert sum(sh["by_sum"].values()) == pytest.approx(1.0)
    assert sum(sh["by_p50"].values()) == pytest.approx(1.0)
    # and it is NOT the old partial denominator
    assert sh["by_sum"]["b"] == pytest.approx(2 / 13)
    assert sh["by_sum"]["b"] != pytest.approx(2 / 3)


def test_shares_are_percentages_of_the_same_whole_in_both_statistics():
    legs = {"a1": [1.0, 9.0], "a2": [1.0, 1.0], "b": [1.0, 1.0]}
    sh = plt.shares(legs)
    assert sum(sh["by_sum"].values()) == pytest.approx(1.0)
    assert sum(sh["by_p50"].values()) == pytest.approx(1.0)
    assert sh["totals_hours"]["a1"] == pytest.approx(10 / 3600)


# --- dominant leg (defect 3) ----------------------------------------------

def test_dominant_leg_is_named_when_both_statistics_agree():
    legs = {"a1": [10 * 3600.0], "a2": [1 * 3600.0], "b": [1 * 3600.0]}
    lead, why = plt.dominant_leg(plt.shares(legs))
    assert lead == "a1"
    assert "both" in why


def test_no_leg_is_named_when_the_statistics_disagree():
    """Mass says b, the typical PR says a1 — the verdict must be stated per
    statistic rather than silently choosing one. The fixture must make the two
    lead DIFFERENT legs by a clear margin, not tie them."""
    legs = {"a1": [5 * 3600.0] * 9 + [1 * 3600.0],   # p50 = 5h, sum = 46h
            "a2": [0.1 * 3600.0] * 10,               # p50 = 0.1h
            "b": [100 * 3600.0] + [0.5 * 3600.0] * 9}  # p50 = 0.5h, sum = 104.5h
    sh = plt.shares(legs)
    assert max(sh["by_sum"], key=lambda k: sh["by_sum"][k]) == "b"
    assert max(sh["by_p50"], key=lambda k: sh["by_p50"][k]) == "a1"
    lead, why = plt.dominant_leg(sh)
    assert lead is None
    assert "no single leg dominates" in why
    assert "by sum" in why and "by p50" in why


# --- window-end placement (the default --days 7 path) ---------------------

def test_a_pr_merged_after_the_window_end_is_open_not_abandoned():
    """A PR created in-window and merged AFTER the window end was still open at
    the window end. Counting it as `C` would inflate the abandonment population
    the docstring warns about; the earlier implementation instead raised here."""
    assert plt.place_by_ends(at(days=8), at(days=8), at(days=7)) == "OPEN"


def test_a_pr_closed_unmerged_after_the_window_end_is_open():
    assert plt.place_by_ends(None, at(days=8), at(days=7)) == "OPEN"


def test_still_open_at_the_window_end_is_open():
    assert plt.place_by_ends(None, None, at(days=7)) == "OPEN"


def test_an_in_window_merge_is_merged():
    assert plt.place_by_ends(at(days=3), at(days=3), at(days=7)) == "MERGED"


def test_an_in_window_close_is_abandonment():
    assert plt.place_by_ends(None, at(days=3), at(days=7)) == "C"


# --- the I/O path: where both P0s actually lived -------------------------

def test_paginated_does_not_assume_a_dict(monkeypatch):
    """The defect: `/pulls` returns a bare JSON array, so `.get()` raised
    AttributeError and the tool never produced a measurement."""
    monkeypatch.setattr(plt, "gh_api", lambda repo, url: [{"n": 1}])
    assert plt.paginated("o/r", "repos/o/r/pulls") == [{"n": 1}]


def test_paginated_follows_a_bare_array_past_the_first_page(monkeypatch):
    pages = {1: [{"n": i} for i in range(plt.PAGE)], 2: [{"n": plt.PAGE}]}
    monkeypatch.setattr(plt, "gh_api",
                        lambda repo, url: pages[int(url.rsplit("page=", 1)[1])])
    assert len(plt.paginated("o/r", "repos/o/r/pulls?state=all")) == plt.PAGE + 1


def test_paginated_handles_a_keyed_endpoint_with_total_count(monkeypatch):
    monkeypatch.setattr(plt, "gh_api",
                        lambda repo, url: {"total_count": 2, "check_runs": [{"n": 1}, {"n": 2}]})
    assert len(plt.paginated("o/r", "repos/o/r/commits/sha/check-runs", "check_runs")) == 2


# --- partition (defect 1) -------------------------------------------------

def test_partition_holds_accepts_an_exhaustive_count():
    counts = {"A1A2B": 201, "E": 1, "D": 10, "C": 228, "OPEN": 163, "UNKNOWN": 0}
    assert plt.partition_holds(counts, 603) is True


def test_partition_rejects_a_dropped_population():
    """The 12 merged PRs that fell in no bucket: 720 != 732 must fail."""
    counts = {"A1A2B": 238, "E": 0, "D": 0, "C": 359, "OPEN": 123, "UNKNOWN": 0}
    assert plt.partition_holds(counts, 732) is False


def test_partition_rejects_double_counting():
    counts = {"A1A2B": 2, "E": 0, "D": 0, "C": 1, "OPEN": 1, "UNKNOWN": 0}
    assert plt.partition_holds(counts, 3) is False


# --- helpers --------------------------------------------------------------

def test_percentile_is_bounded_even_for_tiny_samples():
    assert plt.percentile([5.0], 0.9) == 5.0
    assert plt.percentile([1.0, 2.0], 0.5) == 2.0
    # empty input is never queried (every caller guards on a non-empty leg first)
    with pytest.raises(IndexError):
        plt.percentile([], 0.5)


def test_the_ratio_reference_is_not_an_allow_list_of_reds():
    """A regression guard on the constant itself: the polarity set must contain
    only non-red conclusions, so `red = not in NON_RED` stays sound."""
    assert "failure" not in plt.NON_RED
    assert "timed_out" not in plt.NON_RED
    assert "success" in plt.NON_RED
