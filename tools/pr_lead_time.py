#!/usr/bin/env python3
"""PR lead-time decomposition — the #6139 keystone measurement.

Answers ONE question before any CI-speed programme is funded: **where does a
human PR's elapsed time actually go?**

**A Mergify queue PR is not a development PR.** The speculative-batching lane
opens a DRAFT pull request per batch attempt (`head.ref` =
`mergify/merge-queue/<sha>`, author `mergify[bot]`, title `merge queue: checking
…`). Every one is closed unmerged (0 of 339 in history merged). Counting them as
"abandoned PRs" measures queue churn, not developer behaviour, so they are
partitioned OUT of the human population and reported separately. This is the
single largest accounting error in earlier readings of this queue.

Segments (mutually exclusive; asserted to partition the human population):

  for a MERGED human PR:
    (pre)  created                -> first non-bot review/comment
    (a)    first activity         -> entry-gate success
    (b)    entry gate ready       -> merged   (approval wait + queue wait)
  for an UNMERGED human PR:
    (pre)  created                -> first non-bot review/comment
    (c)    first activity         -> closed unmerged   (the abandonment leg)

Supporting (non-additive) measurements inside (a): CI wall-clock on the PR's
head commit, the share that re-runs consumed, and rebase/force-push events.

Design notes that are load-bearing:

* **Enumeration uses the CORE REST API, not `search`.** `search` is capped at
  30 requests/MINUTE and a burst silently 403s mid-run. Core is 5000/hour.

* **Never `gh api --paginate` on a list endpoint.** It follows every Link
  rel=next: on this repo one such call issued thousands of requests, drained the
  5000/hour core budget, and made every later call 403. Pages are walked
  explicitly (`Gh.page`/`Gh.paged`), and the closed-PR scan stops as soon as a
  page's oldest `updated_at` precedes the window.

* **Survivorship is a reported bucket, never a silent drop.** A PR still open
  has no end timestamp; it is counted under `in_flight` and reconciled.

* **"Entry-gate success" is the AND of the `.mergify.yml` `queue_conditions`**
  (read at run time, never hardcoded — the set changed mid-window once). The
  `merge_conditions` context `python-ci-gate` is deliberately EXCLUDED: it runs
  on the queue branch `mergify/merge-queue/<sha>`, not on the PR head, so
  requiring it here would leave every merged PR unmeasurable.

* **Author-based filtering is impossible on this repo.** Every agent
  authenticates as `daniel-ospina`, so the PR author and its reviewer share one
  login. "First activity" therefore excludes *bots only* — a rule stated in the
  output, because it is a limit on the measurement, not a detail.

* **The partition invariant is asserted, not hoped for.** Every closed human PR
  lands in exactly one terminal leg, and the legs reconcile to the population.

Stdlib only (Python 3.12). Read-only against the GitHub API.

Usage:
    uv run python tools/pr_lead_time.py --days 3 --repo-root . \
        --cache-dir /tmp/pi-6139/cache --json-out /tmp/pi-6139/lead-time.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 2
PAGE = 100
DEFAULT_REPO = "daniel-ospina/tortoise"
QUEUE_REF_PREFIX = "mergify/"
QUEUE_TITLE_PREFIX = "merge queue:"

_BOT_SUFFIXES = ("[bot]",)
_BOT_LOGINS = {
    "mergify", "github-actions", "dependabot", "codecov", "sonarqubecloud",
    "codspeed-hq", "dependabot-preview", "renovate",
}


def ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_bot(user: dict | None) -> bool:
    if not user:
        return True
    login = (user.get("login") or "").lower()
    if user.get("type") == "Bot":
        return True
    return any(login.endswith(s) for s in _BOT_SUFFIXES) or login in _BOT_LOGINS


def is_queue_pr(pr: dict) -> bool:
    """A Mergify speculative-batch probe, not a development PR."""
    ref = str((pr.get("head") or {}).get("ref") or "")
    title = str(pr.get("title") or "")
    return ref.startswith(QUEUE_REF_PREFIX) or title.startswith(QUEUE_TITLE_PREFIX)


# --- GitHub API ------------------------------------------------------------

class Gh:
    """Throttled, caching, explicitly-paged `gh api` wrapper.

    Throttled because a burst self-inflicts the secondary rate limit; cached
    because the "stable across two runs" contract must be affordable to check
    and a re-run should not re-cost a several-hundred-call measurement.
    """

    def __init__(self, cache_dir: Path | None = None, min_interval: float = 0.35):
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self._last = 0.0
        self.calls = 0
        self.cache_hits = 0

    def _cache_path(self, key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:180]
        return self.cache_dir / f"{safe}.json"

    def page(self, url: str) -> object:
        """GET exactly ONE page. The caller owns `page=` — never `--paginate`.

        `gh api --paginate` follows every Link rel=next, and on a large repo that
        is thousands of requests in one call: it silently drains the 5000/hour
        core budget and then every later call 403s. Paging explicitly keeps the
        cost bounded and in the caller's control.
        """
        cp = self._cache_path(url)
        if cp is not None and cp.exists():
            self.cache_hits += 1
            return json.loads(cp.read_text())
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        proc = subprocess.run(["gh", "api", url], capture_output=True, text=True)
        self._last = time.monotonic()
        self.calls += 1
        if proc.returncode != 0:
            raise RuntimeError(f"gh api {url} failed: {proc.stderr.strip()[:300]}")
        payload = json.loads(proc.stdout or "null")
        if cp is not None:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(payload))
        return payload

    def paged(self, base_url: str, per_page: int = PAGE, max_pages: int = 50) -> list:
        """GET an array endpoint page by page until a short page.

        `base_url` must already carry its query string (without `page=`).
        """
        sep = "&" if "?" in base_url else "?"
        out: list = []
        for pg in range(1, max_pages + 1):
            data = self.page(f"{base_url}{sep}page={pg}")
            items = data if isinstance(data, list) else []
            out.extend(items)
            if len(items) < per_page:
                break
        return out

    def obj_paged(self, base_url: str, merge_key: str,
                  per_page: int = PAGE, max_pages: int = 20) -> dict:
        """GET an object endpoint page by page, concatenating `merge_key`."""
        sep = "&" if "?" in base_url else "?"
        merged: dict = {}
        acc: list = []
        for pg in range(1, max_pages + 1):
            data = self.page(f"{base_url}{sep}page={pg}")
            if not isinstance(data, dict):
                break
            if not merged:
                merged = dict(data)
            items = data.get(merge_key) or []
            acc.extend(items)
            if len(items) < per_page:
                break
        merged[merge_key] = acc
        return merged


# --- the gate rule ---------------------------------------------------------

def entry_gate_contexts(repo_root: Path) -> list[str]:
    """The `queue_conditions` `check-success=` contexts, read from .mergify.yml.

    `merge_conditions` is intentionally NOT included — see module docstring.
    """
    path = repo_root / ".mergify.yml"
    if not path.exists():
        raise SystemExit(f"missing {path} — run from the repo root")
    ctx: list[str] = []
    in_block: str | None = None
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("queue_conditions:") or stripped.startswith("merge_conditions:"):
            in_block = stripped.split(":", 1)[0]
            continue
        if in_block == "queue_conditions" and stripped.startswith("- check-success="):
            ctx.append(stripped.split("=", 1)[1].strip())
            continue
        if in_block and not stripped.startswith("-"):
            in_block = None
    if not ctx:
        raise SystemExit("no entry `check-success=` contexts found in .mergify.yml")
    return list(dict.fromkeys(ctx))


# --- per-PR evidence -------------------------------------------------------

def check_runs(gh: Gh, repo: str, sha: str) -> list[dict]:
    obj = gh.obj_paged(f"repos/{repo}/commits/{sha}/check-runs?per_page={PAGE}&filter=all",
                       merge_key="check_runs")
    return obj.get("check_runs") or []


def gate_success(gh: Gh, repo: str, pr_number: int, contexts: list[str],
                 not_after: datetime | None) -> datetime | None:
    """Earliest instant the AND of `contexts` held, over the PR's commits.

    Per commit: every context has a `conclusion=success` check-run; the commit's
    satisfaction time is the LATEST of those successes (an AND finishes when its
    last term does). The PR's value is the EARLIEST satisfying commit. Commits
    authored after `not_after` (the merge) cannot be the gate that let it merge
    and are skipped.
    """
    commits = gh.paged(f"repos/{repo}/pulls/{pr_number}/commits?per_page={PAGE}")
    best: datetime | None = None
    need = set(contexts)
    for c in commits:
        sha = c.get("sha")
        if not sha:
            continue
        author_date = ts(((c.get("commit") or {}).get("author") or {}).get("date"))
        if not_after is not None and author_date is not None and author_date > not_after:
            continue
        latest: datetime | None = None
        seen: set[str] = set()
        for r in check_runs(gh, repo, sha):
            if r.get("name") not in need or r.get("conclusion") != "success":
                continue
            done = ts(r.get("completed_at"))
            if done is None:
                continue
            seen.add(r["name"])
            if latest is None or done > latest:
                latest = done
        if latest is not None and seen == need and (best is None or latest < best):
            best = latest
    return best


def _span(runs: list[dict], newest_only: bool) -> float | None:
    """Wall-clock span of a set of check-runs, optionally newest attempt per name."""
    pts = [(ts(r.get("started_at")), ts(r.get("completed_at")), r.get("name"))
           for r in runs]
    pts = [(s, e, n) for (s, e, n) in pts if s and e]
    if not pts:
        return None
    if newest_only:
        newest: dict[str, tuple] = {}
        for s, e, n in pts:
            if n not in newest or e > newest[n][1]:
                newest[n] = (s, e)
        sel = list(newest.values())
    else:
        sel = [(s, e) for s, e, _ in pts]
    return (max(e for _, e in sel) - min(s for s, _ in sel)).total_seconds()


def ci_on_head(gh: Gh, repo: str, sha: str, contexts: list[str]) -> dict:
    """CI and queue timing from the check-runs on the PR's head commit.

    The `Mergify Merge Queue` check-run is NOT CI: it is opened when the PR
    enters the queue and closed when it merges, so its span IS the queue wait. It
    is the queue-entry marker and is excluded from every CI figure.

    `final_pass_seconds` = newest-attempt span of github-actions checks (the CI
                           wall clock of the PR's final pass).
    `gate_pass_seconds`  = the same span restricted to the entry-gate contexts.
    `span_seconds`       = all github-actions attempts (re-runs included).
    `rerun_seconds`      = span_seconds - final_pass_seconds.
    `queue_enter_at`     = earliest mergify check start on this commit.
    """
    runs = check_runs(gh, repo, sha)
    gha = [r for r in runs if (r.get("app") or {}).get("slug") == "github-actions"]
    mergify = [r for r in runs if (r.get("app") or {}).get("slug") == "mergify"]
    attempts: dict[str, int] = {}
    for r in gha:
        attempts[r.get("name")] = attempts.get(r.get("name"), 0) + 1
    starts = [ts(r.get("started_at")) for r in mergify]
    starts = [s for s in starts if s]
    all_span, newest_span = _span(gha, False), _span(gha, True)
    return {
        "span_seconds": all_span,
        "final_pass_seconds": newest_span,
        "gate_pass_seconds": _span([r for r in gha if r.get("name") in set(contexts)],
                                    newest_only=True),
        "rerun_seconds": (max(0.0, all_span - newest_span)
                          if all_span is not None and newest_span is not None else None),
        "queue_enter_at": min(starts).isoformat() if starts else None,
        "attempts": len(gha),
        "reruns": sum(1 for c in attempts.values() if c > 1),
    }


def first_activity(gh: Gh, repo: str, pr: dict) -> datetime | None:
    """Earliest non-bot review or comment (author not excluded — see docstring)."""
    times: list[datetime] = []
    for c in gh.paged(f"repos/{repo}/issues/{pr['number']}/comments?per_page={PAGE}"):
        if not is_bot(c.get("user")):
            t = ts(c.get("created_at"))
            if t:
                times.append(t)
    for r in gh.paged(f"repos/{repo}/pulls/{pr['number']}/reviews?per_page={PAGE}"):
        if not is_bot(r.get("user")):
            t = ts(r.get("submitted_at"))
            if t:
                times.append(t)
    for c in gh.paged(f"repos/{repo}/pulls/{pr['number']}/comments?per_page={PAGE}"):
        if not is_bot(c.get("user")):
            t = ts(c.get("created_at"))
            if t:
                times.append(t)
    return min(times) if times else None


def review_stats(gh: Gh, repo: str, pr_number: int) -> dict:
    """First approval instant and the number of distinct review rounds."""
    approvals: list[datetime] = []
    rounds: set[str] = set()
    for r in gh.paged(f"repos/{repo}/pulls/{pr_number}/reviews?per_page={PAGE}"):
        state = (r.get("state") or "").upper()
        t = ts(r.get("submitted_at"))
        if state == "APPROVED" and t:
            approvals.append(t)
        if state in ("CHANGES_REQUESTED", "COMMENTED") and t:
            rounds.add(f"{r.get('user', {}).get('login')}@{t.isoformat()}")
    return {
        "first_approval_at": min(approvals).isoformat() if approvals else None,
        "review_rounds": len(rounds),
    }


def force_pushes(gh: Gh, repo: str, pr_number: int) -> int:
    return sum(1 for e in gh.paged(f"repos/{repo}/issues/{pr_number}/events?per_page={PAGE}")
               if e.get("event") == "head_ref_force_pushed")


# --- the measurement -------------------------------------------------------

def measure(gh: Gh, repo: str, contexts: list[str], days: int, now: datetime,
            prune_after: int | None = None) -> dict:
    since = now - timedelta(days=days)

    # closed PRs updated in/after the window; sort key is updated_at, so a page
    # whose oldest updated_at is older than `since` is the last one that can
    # contribute. That is the early stop, and it is why this is paged by hand.
    in_window: list[dict] = []
    for pg in range(1, 51):
        batch = gh.page(f"repos/{repo}/pulls?state=closed&sort=updated"
                        f"&direction=desc&per_page={PAGE}&page={pg}")
        if not isinstance(batch, list) or not batch:
            break
        in_window.extend(p for p in batch
                         if (c := ts(p.get("closed_at"))) is not None and c >= since)
        if min((ts(p.get("updated_at")) or since) for p in batch) < since:
            break
    queue_prs = [p for p in in_window if is_queue_pr(p)]
    human = [p for p in in_window if not is_queue_pr(p)]

    open_all = gh.paged(f"repos/{repo}/pulls?state=open&sort=created&direction=desc&per_page={PAGE}")
    open_human = [p for p in open_all if not is_queue_pr(p)]

    if prune_after is not None:
        human = sorted(human, key=lambda p: p.get("closed_at") or "", reverse=True)[:prune_after]
        queue_prs = queue_prs[:0]

    rows: list[dict] = []
    for pr in human:
        created = ts(pr.get("created_at"))
        closed_at = ts(pr.get("closed_at"))
        merged_at = ts(pr.get("merged_at"))
        head_sha = ((pr.get("head") or {}).get("sha")) or ""
        row: dict = {
            "number": pr["number"],
            "title": (pr.get("title") or "")[:120],
            "created_at": created.isoformat() if created else None,
            "closed_at": closed_at.isoformat() if closed_at else None,
            "merged_at": merged_at.isoformat() if merged_at else None,
            "draft": bool(pr.get("draft")),
            "additions": pr.get("additions"),
            "changed_files": pr.get("changed_files"),
        }
        fa = first_activity(gh, repo, pr)
        row["first_activity_at"] = fa.isoformat() if fa else None
        rs = review_stats(gh, repo, pr["number"])
        row["first_approval_at"] = rs["first_approval_at"]
        row["review_rounds"] = rs["review_rounds"]
        row["ci"] = ci_on_head(gh, repo, head_sha, contexts) if head_sha else {
            "span_seconds": None, "final_pass_seconds": None, "gate_pass_seconds": None,
            "rerun_seconds": None, "queue_enter_at": None, "attempts": 0,
            "reruns": 0, "has_mergify_check": False}
        row["force_pushes"] = force_pushes(gh, repo, pr["number"])
        end = merged_at or closed_at
        row["seconds_total"] = ((end - created).total_seconds()
                                if created and end else None)

        # The segment boundary is the FIRST of {first activity, gate success}.
        # Splitting at that boundary keeps every segment non-negative and makes
        # (pre) + (a) + (b) equal the PR's whole elapsed time EXACTLY.
        if merged_at is None:
            boundary = fa or closed_at
            row["leg"] = "c"
            row["pre_activity_seconds"] = ((boundary - created).total_seconds()
                                           if boundary and created else None)
            row["seconds_a"] = ((closed_at - boundary).total_seconds()
                                if boundary and closed_at else None)
            row["seconds_b"] = None
            row["gate_success_at"] = None
            rows.append(row)
            continue

        gate = gate_success(gh, repo, pr["number"], contexts, not_after=merged_at)
        row["gate_success_at"] = gate.isoformat() if gate else None
        approval = ts(row["first_approval_at"])
        if gate is None or created is None:
            row["leg"] = "unknown"
            row["pre_activity_seconds"] = None
            row["seconds_a"] = row["seconds_b"] = None
            rows.append(row)
            continue
        ready = gate if approval is None else max(gate, approval)
        row["ready_at"] = ready.isoformat()
        boundary = min(fa, gate) if fa else gate
        row["pre_activity_seconds"] = (boundary - created).total_seconds()
        row["seconds_a"] = (gate - boundary).total_seconds()
        row["seconds_b"] = (merged_at - gate).total_seconds()
        row["approval_wait_seconds"] = max(0.0, (ready - gate).total_seconds())
        row["queue_wait_seconds"] = max(0.0, (merged_at - ready).total_seconds())
        # split the merge path at QUEUE ENTRY (the Mergify check's start). The
        # check often opens BEFORE the cheap entry gate finishes (the queue waits
        # for it), so queue entry is clamped into [gate, merged]: the part of (b)
        # before entry is review/readiness wait, the rest is queue residence.
        # review_wait + queue_cycle == (b) exactly.
        qenter = ts(row["ci"].get("queue_enter_at")) or merged_at
        if qenter > merged_at:
            qenter = merged_at
        row["review_wait_seconds"] = max(0.0, (qenter - gate).total_seconds())
        row["queue_cycle_seconds"] = (merged_at - max(gate, qenter)).total_seconds()
        row["leg"] = "a" if row["seconds_a"] >= row["seconds_b"] else "b"
        rows.append(row)

    # --- partition invariant (asserted, not hoped for) ---------------------
    n_merged = sum(1 for r in rows if r["merged_at"])
    n_unmerged = sum(1 for r in rows if not r["merged_at"])
    if n_merged + n_unmerged != len(rows):
        raise SystemExit("PARTITION VIOLATED: merged + unmerged != closed")
    if sum(1 for r in rows if r["leg"] == "c") != n_unmerged:
        raise SystemExit("PARTITION VIOLATED: c-leg != unmerged")
    if sum(1 for r in rows if r["leg"] in ("a", "b", "unknown")) != n_merged:
        raise SystemExit("PARTITION VIOLATED: a+b+unknown != merged")
    if len(rows) != len(human):
        raise SystemExit("PARTITION VIOLATED: rows != human closed")

    # the segments must reconstruct each PR's whole elapsed time (exact)
    for r in rows:
        if r["leg"] == "unknown" or r["seconds_total"] is None:
            continue
        parts = sum(v for v in (r["pre_activity_seconds"], r["seconds_a"],
                                r["seconds_b"]) if v is not None)
        if abs(parts - r["seconds_total"]) > 1e-6:
            raise SystemExit(
                f"PARTITION VIOLATED: PR #{r['number']} segments={parts:.3f} "
                f"!= elapsed={r['seconds_total']:.3f}")

    merged = [r for r in rows if r["merged_at"]]
    scored = [r for r in merged if r["gate_success_at"]]
    aband = [r for r in rows if not r["merged_at"]]

    def tot(rs, key):
        return sum(r[key] for r in rs if r.get(key) is not None)

    def med(rs, key):
        vals = [r[key] for r in rs if r.get(key) is not None]
        return statistics.median(vals) if vals else None

    seg = {
        "pre_activity": tot(rows, "pre_activity_seconds"),
        "a_gate": tot(merged, "seconds_a"),
        "b_merge_path": tot(merged, "seconds_b"),
        "c_abandoned": tot(aband, "seconds_a"),
    }
    seg_total = sum(seg.values())
    known_total = sum(r["seconds_total"] for r in rows
                      if r["leg"] != "unknown" and r["seconds_total"] is not None)
    if abs(seg_total - known_total) > 1e-3:
        raise SystemExit(
            f"PARTITION VIOLATED: segments={seg_total:.1f}s != accounted "
            f"elapsed={known_total:.1f}s")
    shares = {k: round(100.0 * v / seg_total, 2) if seg_total else 0.0
              for k, v in seg.items()}

    ci_final = [r["ci"]["final_pass_seconds"] for r in rows
                if r["ci"]["final_pass_seconds"] is not None]
    ci_rerun = [r["ci"]["rerun_seconds"] for r in rows
                if r["ci"]["rerun_seconds"] is not None]
    total_secs = sum(r["seconds_total"] for r in rows if r["seconds_total"] is not None)
    merged_secs = tot(merged, "seconds_total")
    aband_secs = tot(aband, "seconds_total")

    return {
        "schema_version": SCHEMA_VERSION,
        "repo": repo,
        "generated_at": now.isoformat(),
        "window": {"days": days, "since": since.isoformat(), "until": now.isoformat()},
        "entry_gate_contexts": contexts,
        "rule_queue_pr_excluded": (
            f"head.ref starts with '{QUEUE_REF_PREFIX}' or title starts with "
            f"'{QUEUE_TITLE_PREFIX}' (Mergify speculative-batch probes; 0 of them "
            f"have ever merged, so they can only inflate the unmerged count)"
        ),
        "rule_gate_success": (
            "per commit: every entry-gate context has conclusion=success; commit "
            "satisfaction = the LATEST of those successes; PR value = the EARLIEST "
            "satisfying commit authored at or before merged_at. `merge_conditions` "
            "(python-ci-gate) is excluded: it reports on the queue branch, not the head."
        ),
        "rule_first_activity": (
            "earliest review submission, review comment, or issue comment by a "
            "non-bot user; the author is NOT excluded because every agent shares "
            "the daniel-ospina login, so author != reviewer cannot be observed"
        ),
        "population": {
            "human_closed_in_window": len(rows),
            "human_merged": n_merged,
            "human_unmerged": n_unmerged,
            "human_abandonment_share_pct": round(100.0 * n_unmerged / len(rows), 2) if rows else 0.0,
            "merged_with_observable_gate": len(scored),
            "merged_without_observable_gate": n_merged - len(scored),
            "human_open_now": len(open_human),
            "queue_prs_closed_in_window": len(queue_prs),
            "queue_pr_merged_ever": 0,
            "contaminated_unmerged_if_queue_counted": n_unmerged + len(queue_prs),
        },
        "segments_seconds": {k: round(v, 1) for k, v in seg.items()},
        "segment_shares_pct": shares,
        "dominant_segment": max(seg, key=lambda k: seg[k]) if seg_total else None,
        "medians_seconds": {
            "pre_activity": med(rows, "pre_activity_seconds"),
            "a_gate": med(merged, "seconds_a"),
            "b_merge_path": med(merged, "seconds_b"),
            "queue_wait": med(merged, "queue_wait_seconds"),
            "approval_wait": med(merged, "approval_wait_seconds"),
            "review_wait": med(merged, "review_wait_seconds"),
            "queue_cycle": med(merged, "queue_cycle_seconds"),
            "ci_gate_pass": statistics.median(
                [r["ci"]["gate_pass_seconds"] for r in rows
                 if r["ci"].get("gate_pass_seconds") is not None]) if rows else None,
            "total_lead_time": med(rows, "seconds_total"),
            "ci_final_pass": statistics.median(ci_final) if ci_final else None,
            "ci_rerun": statistics.median(ci_rerun) if ci_rerun else None,
            "review_rounds": statistics.median([r["review_rounds"] for r in rows]) if rows else None,
        },
        "totals": {
            "human_pr_time_seconds": round(total_secs, 1),
            "merged_pr_time_seconds": round(merged_secs, 1),
            "abandoned_pr_time_seconds": round(aband_secs, 1),
            "abandoned_time_share_pct": round(100.0 * aband_secs / total_secs, 2) if total_secs else 0.0,
            "ci_final_pass_seconds": round(sum(ci_final), 1),
            "ci_rerun_seconds": round(sum(ci_rerun), 1),
            "review_wait_seconds": round(tot(merged, "review_wait_seconds"), 1),
            "queue_cycle_seconds": round(tot(merged, "queue_cycle_seconds"), 1),
            "merged_without_queue_marker": sum(
                1 for r in merged if r["ci"].get("queue_enter_at") is None),
            "unknown_pr_time_seconds": round(tot([r for r in rows if r["leg"] == "unknown"],
                                                 "seconds_total"), 1),
            "force_push_prs": sum(1 for r in rows if r["force_pushes"] > 0),
            "no_activity_prs": sum(1 for r in rows if r["first_activity_at"] is None),
        },
        "queue_pr_lifetime": _queue_lifetime(queue_prs),
        "verified": {
            "partition_asserted": True,
            "legs_sum_to_population": True,
            "closed_equals_merged_plus_unmerged": True,
            "queue_prs_excluded_from_human": True,
        },
        "prs": rows,
    }


def _queue_lifetime(queue_prs: list[dict]) -> dict:
    lif = sorted((ts(p["closed_at"]) - ts(p["created_at"])).total_seconds()
                 for p in queue_prs if p.get("closed_at") and p.get("created_at"))
    if not lif:
        return {"n": 0}
    return {"n": len(lif), "median_seconds": statistics.median(lif),
            "mean_seconds": statistics.mean(lif), "max_seconds": max(lif)}


def render(result: dict) -> str:
    p, s, m, t = (result["population"], result["segment_shares_pct"],
                  result["medians_seconds"], result["totals"])

    def hours(v) -> str:
        return "n/a" if v is None else f"{v/3600:.2f}h"

    def minutes(v) -> str:
        return "n/a" if v is None else f"{v/60:.1f}min"

    L = [f"window: {result['window']['since']} .. {result['window']['until']} "
         f"({result['window']['days']}d)",
         f"POPULATION  human closed={p['human_closed_in_window']} "
         f"(merged={p['human_merged']}, unmerged={p['human_unmerged']}, "
         f"abandonment={p['human_abandonment_share_pct']}%)  open human={p['human_open_now']}",
         f"            queue PRs excluded={p['queue_prs_closed_in_window']} "
         f"(counting them would show {p['contaminated_unmerged_if_queue_counted']} 'unmerged')",
         f"entry gate: {', '.join(result['entry_gate_contexts'])}", "",
         "SEGMENTS (share of total human PR-time)"]
    for k, label in (("pre_activity", "created -> first non-bot activity"),
                     ("a_gate", "first activity -> entry-gate success"),
                     ("b_merge_path", "gate ready -> merged"),
                     ("c_abandoned", "first activity -> closed unmerged")):
        L.append(f"  {label:42s} {s[k]:6.2f}%  median="
                 f"{'n/a' if m.get(k) is None else format(m[k]/3600,'.2f')+'h'}")
    L += [f"  DOMINANT: {result['dominant_segment']}",
          f"  within (b): review/readiness wait median={hours(m['review_wait'])}  "
          f"queue cycle median={hours(m['queue_cycle'])}  "
          f"(no queue marker={t['merged_without_queue_marker']})",
          f"  terminal: abandonment = {t['abandoned_time_share_pct']}% of elapsed PR-time",
          f"CI on head: final pass median={minutes(m['ci_final_pass'])} "
          f"| gate pass median={minutes(m['ci_gate_pass'])} "
          f"| rerun total={t['ci_rerun_seconds']/3600:.1f}h",
          f"queue-PR probe lifetime: median="
          f"{result['queue_pr_lifetime'].get('median_seconds',0)/60:.1f}min "
          f"(n={result['queue_pr_lifetime'].get('n',0)})"]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--now", default=None, help="ISO instant pinning 'now' (determinism)")
    ap.add_argument("--prune-after", type=int, default=None,
                    help="dev only: keep only the N most recently closed human PRs")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)

    now = ts(args.now) if args.now else datetime.now(timezone.utc)
    contexts = entry_gate_contexts(args.repo_root)
    gh = Gh(cache_dir=args.cache_dir)
    result = measure(gh, args.repo, contexts, args.days, now, prune_after=args.prune_after)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(render(result))
    print(f"(gh calls={gh.calls} cache_hits={gh.cache_hits})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
