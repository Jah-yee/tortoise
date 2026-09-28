#!/usr/bin/env python3
"""PR lead-time decomposition (#6139).

Answers the keystone question for epic #5215: *is CI duration the binding
constraint on the merge queue, or is the wait somewhere else?* — a cheap test
standing in front of an expensive sharding programme.

A merged PR's life is decomposed into three consecutive, non-overlapping legs,
all measured **on the head the PR merged with**:

    (a1) created  -> that head's FIRST check started   a RESIDUAL
    (a2) first check -> gate green                     == CI WALL-CLOCK
    (b)  gate green -> merged                          post-green wait

read together with two terminal buckets that must not be silently dropped:

    (c)  created -> closed unmerged
    OPEN still open at the window end

**(a1) is a residual, not "author latency".** It contains every *non-merging*
head's CI span and the first check's scheduling latency, so it is an upper
bound on author iteration, not a measurement of it. Do not read the verdict as
"a faster gate cannot touch (a1)" — it can, via the discarded heads.

Shares are reported over the FULL merged life (a1 + a2 + b) in two statistics —
by SUM (which leg holds the queue's total wait) and by P50 (the typical PR's
shape) — and a leg is only called dominant if it leads under both. On the
populations measured so far the two agree on the leader; where they disagree,
report per statistic rather than picking one.

Accounting discipline (this is the tool's whole risk surface — a silent drop
fails OPEN by shrinking a leg):
  - the partition is ASSERTED mutually exclusive and exhaustive;
  - the `pulls` sweep is checked for COVERAGE (it must reach back past the
    window start), not for headroom — the sweep paginates, so headroom is not
    the question; what matters is that the fetched slice spans the window;
  - fetch failures are COUNTED and derived figures are reported as LOWER
    BOUNDS with their dropout count, never swallowed into a leg;
  - a context ABSENT from a head is reported distinctly from one PRESENT but
    never successful, and the repo's own rail polarity is applied
    (`cancelled`/`stale` are NOT red).

**The abandoned population is not developer behaviour.** 93.4% of closed-
unmerged PRs are drafts closed in same-minute batches — Mergify speculative-
batch QUEUE-PROBE PRs (`mergify[bot]`, `mergify/merge-queue/*`, 0 of 339 ever
merged). Excluding them, human abandonment is ~8% over 7d. Do not report the
raw closed-unmerged rate as abandonment.

Read-only. Measurement only — it gates nothing.
Exit: 0 measured and self-consistent · 1 the partition or an invariant failed
      · 2 UNKNOWN (unobserved read: protection unreadable, or the fetched slice
      does not cover the window) — never 0 on an unobserved read.

Usage:
    python3 tools/pr_lead_time.py --window 2026-09-21T00:00:00Z 2026-09-28T00:00:00Z
    python3 tools/pr_lead_time.py --days 7 --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_timing import gh_api

DEFAULT_REPO = "daniel-ospina/tortoise"
PAGE = 100

# The repo's own rail polarity (the merge rail in scripts/ is keyed on (app, name)).
# A conclusion not listed here is RED — including conclusions GitHub has not
# documented and a null one. Do NOT invert this into an allow-list of reds.
NON_RED = frozenset({"success", "neutral", "skipped", "cancelled", "stale"})

GREEN = "green"
NON_RED_NOT_GREEN = "non_red_not_green"   # e.g. only ever cancelled/stale: not a failure, not a pass
RED = "red"
NEVER_RAN = "never_ran"


# --- pure decomposition (no I/O; this is what the tests exercise) ----------

def parse_ts(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def classify_contexts(seen: dict[str, list[str]]) -> dict[str, str]:
    """Per required context: green | non_red_not_green | red.

    `seen` maps a context name to every conclusion observed on that head. A
    context with a success is green. Otherwise, if EVERY observed conclusion is
    non-red, it is non_red_not_green; any other conclusion means RED.
    """
    out: dict[str, str] = {}
    for name, conclusions in seen.items():
        observed = [(c or "null") for c in conclusions]
        if "success" in observed:
            out[name] = GREEN
        elif all(c in NON_RED for c in observed):
            out[name] = NON_RED_NOT_GREEN
        else:
            out[name] = RED
    return out


def head_state(required: list[str], seen: dict[str, list[str]]) -> dict:
    """Classify one head. `green_at` is set only when EVERY required context is green."""
    states = classify_contexts(seen)
    never_ran = sorted(c for c in required if c not in seen)
    red = sorted(c for c, s in states.items() if s == RED)
    non_red_not_green = sorted(c for c, s in states.items() if s == NON_RED_NOT_GREEN)
    return {
        "never_ran": never_ran,
        "red": red,
        "non_red_not_green": non_red_not_green,
        "greened": not never_ran and not red and not non_red_not_green,
        "conclusions": {k: sorted(set(v)) for k, v in seen.items()},
    }


def decompose(created: datetime, merged: datetime, first_check: datetime | None,
              green_at: datetime | None) -> tuple[str, dict | None]:
    """Bucket one merged PR. Returns (bucket, legs-or-None).

    ``E`` is "merged AND the gate later fully greened" — the gate must have
    greened, so a PR whose head never greened is ``D`` even though it too was
    merged before its gate finished.
    """
    if green_at is None:
        return "D", None            # head never fully greened
    if green_at > merged:
        return "E", None            # merged before the gate went green
    if first_check is None:
        return "UNKNOWN", None      # no check-run start recorded: do not invent one
    legs = {
        "a1": (first_check - created).total_seconds(),
        "a2": (green_at - first_check).total_seconds(),
        "b": (merged - green_at).total_seconds(),
    }
    # Explicit raises, not `assert`: an assert vanishes under `python -O`, and
    # these catch bad timestamps that would otherwise become a real leg value.
    if first_check < created:
        raise ValueError("first check predates creation")
    if any(v < 0 for v in legs.values()):
        raise ValueError(f"negative leg: {legs}")
    return "A1A2B", legs


def place_by_ends(merged: datetime | None, closed: datetime | None,
                  end: datetime) -> str:
    """Which end-bucket a PR's timestamps put it in, relative to the window end.

    A PR whose end event fell AFTER ``end`` was still OPEN at the window end — it
    is a survivor, not an abandonment. Getting this wrong is not cosmetic: a PR
    created in-window and merged after the window end would otherwise be counted
    as ``C`` (closed unmerged), inflating the abandonment population that this
    module's docstring exists to warn about.
    """
    if merged is None and closed is None:
        return "OPEN"            # no end event at all: still open at the window end
    if merged is not None:
        return "OPEN" if merged >= end else "MERGED"
    if closed is not None and closed >= end:
        return "OPEN"
    return "C"


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def shares(legs: dict[str, list[float]]) -> dict:
    """Shares of the FULL merged life, by sum and by p50. Never a partial denominator."""
    totals = {k: sum(v) for k, v in legs.items()}
    medians = {k: percentile(v, 0.5) for k, v in legs.items()}
    tot_sum, tot_med = sum(totals.values()), sum(medians.values())
    return {
        "by_sum": {k: (totals[k] / tot_sum if tot_sum else 0.0) for k in legs},
        "by_p50": {k: (medians[k] / tot_med if tot_med else 0.0) for k in legs},
        "totals_hours": {k: totals[k] / 3600 for k in legs},
        "medians_hours": {k: medians[k] / 3600 for k in legs},
    }


def partition_holds(counts: dict[str, int], population: int) -> bool:
    return sum(counts.values()) == population


def dominant_leg(sh: dict) -> tuple[str | None, str]:
    """Name the leading leg only when the two statistics agree on it."""
    lead_sum = max(sh["by_sum"], key=lambda k: sh["by_sum"][k])
    lead_med = max(sh["by_p50"], key=lambda k: sh["by_p50"][k])
    if lead_sum == lead_med:
        return lead_sum, f"{lead_sum} leads under both statistics"
    return None, (f"no single leg dominates: {lead_sum} leads by sum, {lead_med} by p50 "
                  f"— report per statistic")


# --- I/O ------------------------------------------------------------------

def paginated(repo: str, url: str, key: str | None = None) -> list[dict]:
    """Own the sweep: gh_api is a SINGLE call and does not paginate.

    GitHub returns some list endpoints as a bare JSON ARRAY (``/pulls``) and
    others as an object with the items under a key plus ``total_count``
    (``/check-runs``). Both shapes are handled — assuming one silently breaks the
    other, which is exactly how this tool was first written.
    """
    out: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        data = gh_api(repo, f"{url}{sep}per_page={PAGE}&page={page}")
        items = data if isinstance(data, list) else data.get(key or "", [])
        out.extend(items)
        if not items:
            return out
        if isinstance(data, dict) and len(out) >= data.get("total_count", 0):
            return out
        if isinstance(data, list) and len(items) < PAGE:
            return out
        page += 1


def required_contexts(repo: str) -> list[str] | None:
    """Branch protection's required contexts, or None if unreadable/empty.

    Returns None (not a fallback list) on an empty/null response: a runtime
    read cannot establish the set's CONSTANCY over a past window anyway, so
    silently substituting a remembered set would manufacture confidence.
    """
    try:
        data = gh_api(repo, f"repos/{repo}/branches/main/protection")
    except Exception as exc:
        print(f"2: could not read branch protection: {exc}", file=sys.stderr)
        return None
    contexts = (data.get("required_status_checks") or {}).get("contexts")
    if not contexts:
        print("2: branch protection returned an empty/null required-context list", file=sys.stderr)
        return None
    return list(contexts)


def main() -> int:
    ap = argparse.ArgumentParser(description="PR lead-time decomposition (#6139)")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--window", nargs=2, metavar=("START", "END"),
                    help="ISO bounds (inclusive start, exclusive end) — pinned for reproducibility")
    ap.add_argument("--days", type=int, default=7,
                    help="window length when --window is omitted (end floored to midnight UTC)")
    ap.add_argument("--json", metavar="PATH", help="also write the raw result here")
    args = ap.parse_args()

    if args.window:
        start, end = (parse_ts(w) for w in args.window)
    else:
        end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=args.days)

    required = required_contexts(args.repo)
    if required is None:
        return 2
    print(f"required contexts (read from branch protection NOW): {required}")
    print(f"window (pinned): {start.isoformat()} .. {end.isoformat()}")
    print("  NOTE: a runtime read cannot establish the set's constancy across this window;")
    print("        treat the required set as an ASSUMPTION, not a checked fact.")

    pulls = paginated(args.repo, f"repos/{args.repo}/pulls?state=all&sort=created&direction=desc")
    if not pulls:
        print("2: no pulls returned", file=sys.stderr)
        return 2
    created = [parse_ts(p["created_at"]) for p in pulls]
    oldest = min(created)
    print(f"pulls fetched={len(pulls)}; oldest created={oldest.date()} vs window start {start.date()}")
    if oldest >= start:
        print("2: COVERAGE FAILED — the fetched slice does not reach back past the window start; "
              "paginate further before trusting any count", file=sys.stderr)
        return 2
    print("  COVERAGE OK: the slice reaches back past the window start")

    in_window = [p for p in pulls if start <= (parse_ts(p["created_at"]) or start) < end]
    counts = {"A1A2B": 0, "E": 0, "D": 0, "UNKNOWN": 0, "C": 0, "OPEN": 0}
    legs: dict[str, list[float]] = {"a1": [], "a2": [], "b": []}
    c_times: list[float] = []
    dropouts = {"merged_heads": 0, "closed_heads": 0}
    d_detail: dict[str, list[dict]] = {}

    for p in in_window:
        cr, mg, cl = (parse_ts(p["created_at"]), parse_ts(p["merged_at"]), parse_ts(p["closed_at"]))
        where = place_by_ends(mg, cl, end)
        if where == "OPEN":
            # Still open at the window end (or ended after it) — a survivor.
            counts["OPEN"] += 1
            continue
        if where == "C":
            counts["C"] += 1
            c_times.append((cl - cr).total_seconds())
            continue
        head = p["head"]["sha"]
        try:
            runs = paginated(args.repo, f"repos/{args.repo}/commits/{head}/check-runs"
                                        f"?filter=all", "check_runs")
        except Exception:
            dropouts["merged_heads"] += 1
            counts["UNKNOWN"] += 1
            continue
        seen: dict[str, list[str]] = {}
        first_check = None
        earliest_success: dict[str, datetime] = {}
        for r in runs:
            name = (r.get("name") or "").strip()
            started = parse_ts(r.get("started_at"))
            if started and (first_check is None or started < first_check):
                first_check = started
            if name in required:
                seen.setdefault(name, []).append(r.get("conclusion") or "null")
                if r.get("conclusion") == "success" and r.get("completed_at"):
                    when = parse_ts(r["completed_at"])
                    if name not in earliest_success or when < earliest_success[name]:
                        earliest_success[name] = when
        state = head_state(required, seen)
        green_at = max(earliest_success.values()) if state["greened"] and earliest_success else None
        bucket, lg = decompose(cr, mg, first_check, green_at)
        counts[bucket] += 1
        if lg:
            for k in legs:
                legs[k].append(lg[k])
        elif bucket == "D":
            d_detail[str(p["number"])] = {"never_ran": state["never_ran"], "red": state["red"],
                                          "conclusions": state["conclusions"]}

    result = {
        "repo": args.repo,
        "window": [start.isoformat(), end.isoformat()],
        "run_at": datetime.now(UTC).isoformat(),
        "required_contexts": required,
        "population": len(in_window),
        "counts": counts,
        "dropouts": dropouts,
        "legs": legs,
        "bucket_d": d_detail,
        "note": ("legs are measured on each PR's CURRENT head (headRefOid), which is not provably "
                 "the head that merged; the abandoned population includes Mergify queue-probe PRs "
                 "and is NOT a measure of developer abandonment"),
    }

    print(f"\nPOPULATION(created in window)={len(in_window)}")
    print("  buckets: " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    if not partition_holds(counts, len(in_window)):
        print(f"1: PARTITION VIOLATED — buckets sum to {sum(counts.values())}, "
              f"population is {len(in_window)}", file=sys.stderr)
        return 1
    print("  PARTITION OK (mutually exclusive, exhaustive, asserted)")

    if leg := legs["a1"]:
        sh = shares(legs)
        print(f"\nlegs (n={len(leg)}): a1 p50={percentile(legs['a1'], .5)/3600:.2f}h  "
              f"a2 p50={percentile(legs['a2'], .5)/3600:.2f}h  "
              f"b p50={percentile(legs['b'], .5)/3600:.2f}h")
        for stat in ("by_sum", "by_p50"):
            print(f"  shares {stat}: " + "  ".join(f"{k}={v*100:.1f}%" for k, v in sh[stat].items()))
        lead, why = dominant_leg(sh)
        print(f"  dominant: {why}")
        result["shares"] = sh
        result["dominant_leg"] = lead
    else:
        print("\nno fully-greened merged PRs in the window — no shares to report", file=sys.stderr)

    if dropouts["merged_heads"]:
        print(f"\nWARNING: {dropouts['merged_heads']} merged heads could not be fetched; "
              f"those PRs are counted as UNKNOWN, never folded into a leg")
    if c_times:
        print(f"(c) created->closed-unmerged: n={len(c_times)} p50={percentile(c_times, .5)/3600:.2f}h")
        print("    REMINDER: this population is dominated by Mergify queue-probe PRs, not humans")
    if d_detail:
        print(f"\nbucket D ({len(d_detail)}) — merged with a required gate that never succeeded:")
        for num, d in sorted(d_detail.items()):
            print(f"    #{num}: never_ran={d['never_ran']} red={d['red']}")

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, default=str) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
