"""Render the local capture breadcrumb for the AGENT session (#4041).

``~/.tortoise/capture-errors/<harness>.json`` is written by two authors — the
shipped shell hooks (``kind: install-inert``) and
``tortoise.__main__._record_capture_error`` (``kind: capture-failure``) — and
until now NOTHING read it back to the user's agent: the failure was observable
only on the machine that produced it (and, for the server-side half, on the
dashboard the owner explicitly rejected as the surface).

This module is the Python half of the renderer, and it renders ONE kind:
``capture-failure``.  The agent-facing payload is ONE four-line shape shared
with the shell half (``_render_breadcrumb_inert`` in
``tortoise/claude-hooks/session-start.sh``)::

    code:     capture-failure                   (the existing ``kind``)
    what:     Tortoise memory for this project has NOT been filed since <stamp>.
    why:      <the breadcrumb's ``detail``, bounded to one line>
    next:     Recovery: <available recovery actions>

The split is deliberate and load-bearing:

* the ``install-inert`` record is reached BECAUSE the interpreter or the module
  dir could not be resolved, so a Python-only renderer could never report it —
  that half MUST be pure shell, and the shell owns BOTH the record's rendering
  and its recovery text.  There is deliberately no Python copy of the
  ``install-inert`` prose: a duplicated string with no test is how the two
  drift, and the shell half cannot call into Python anyway;
* a ``capture-failure`` record is always written by Python, so Python IS
  available there, and it is the half that needs
  :func:`tortoise.security.redact_secrets`: an error string can carry a token.

The ``kind`` is decided HERE, by parsing the record as JSON — never by a shell
text match.  The start-of-line ``sed`` gate this replaces silently discarded a
compact single-line record (the writer's ``indent=2`` is not a contract), which
silently reintroduced the exact "nobody is told" defect #4041 exists to fix, and
it disabled the whole feature wherever ``sed``/``head`` were absent even though
the interpreter this half exists to use WAS present.

⛔ Phrasing is FACTUAL STATEMENTS, never imperatives — this is VENDOR-MANDATED,
not a style preference.  Claude Code's hook documentation warns that hook output
framed as out-of-band system commands triggers Claude's prompt-injection
defences, which makes Claude surface the text to the user instead of treating it
as injected context.  The recovery half is therefore written as *available
actions* (``Recovery: `tortoise session drain` retries now``) and never as
commands (``Run `tortoise session drain` now``).  Do not "fix" this into
imperatives: an imperative costs the whole payload its context-injection path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tortoise.hook_install import KIND_CAPTURE_FAILURE
from tortoise.security import redact_secrets

#: The bounded length of the rendered ``why`` line.  A breadcrumb detail is an
#: error string — it can be a whole traceback — so it is collapsed to ONE line
#: and capped here rather than dumped into the session context verbatim.
MAX_DETAIL_CHARS = 400

#: The field label column: ``code:``/``what:``/``why:``/``next:`` all start
#: their value at this column, so the payload reads as a table.
_FIELD_WIDTH = 10

#: What happened, for a human AND an agent with no product context.  Deliberately
#: says NOT been filed, never "failed": the capture is spooled and retried by
#: design (``tortoise.capture_spool``), so "failed" would be untrue and would
#: contradict the spool's whole purpose (#4041).
_WHAT = ("Tortoise memory for this project has NOT been filed since {stamp}. "
         "{harness} capture is affected.")

#: What to do about it — available recovery ACTIONS, never commands (see the
#: module docstring: vendor-mandated phrasing for Claude Code hooks).  The
#: ``install-inert`` recovery text lives only in the shell renderer, which owns
#: that record's path (see the module docstring).
_RECOVERY = (
    "Recovery: `tortoise session drain` retries filing from the local "
    "spool now; `tortoise doctor` reports capture health; capture is "
    "enabled with TORTOISE_CAPTURE=1. Memory is not filed until a retry "
    "succeeds, and the turns stay spooled locally meanwhile."
)


def one_line(value: Any) -> str:
    """A rendered scalar as ONE line, whitespace-collapsed.

    Used for ``harness``/``recorded_at``: neither is redacted, and a newline in
    either would forge an extra ``next:``-looking line, breaking the ONE
    four-line payload invariant the agent's context depends on.
    """
    return " ".join(str(value).split())


def bound_detail(detail: Any) -> str:
    """The ``detail`` as ONE bounded, redacted line.

    Redaction runs BEFORE the bound, so a credential-shaped span is replaced
    whole and a truncation can only cut the ``[REDACTED:<kind>]`` marker — never
    leave a suffix of the secret in cleartext.  Whitespace (including newlines)
    is collapsed to single spaces, and the result is capped at
    :data:`MAX_DETAIL_CHARS`.
    """
    text = "" if detail is None else str(detail)
    redacted, _counts = redact_secrets(text)
    text = " ".join(redacted.split())
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS].rstrip() + "…"
    return text


def render(record: dict[str, Any]) -> str:
    """The agent-facing payload for a breadcrumb record, or ``""``.

    ``""`` is the safe answer for anything that is not ``capture-failure`` (no
    new taxonomy — #4041, and ``install-inert`` is the shell renderer's): the
    caller must render nothing rather than invent a code or prose-match the
    record.  This is also the sole ``kind`` gate on the resolved path, so a
    STALE ``install-inert`` record is refused here rather than by a shell text
    match (see the module docstring).
    """
    kind = record.get("kind")
    if kind != KIND_CAPTURE_FAILURE:
        return ""
    harness = one_line(record.get("harness") or "unknown")
    stamp = one_line(record.get("recorded_at") or "an unrecorded time")
    lines = (
        f"{'code:':<{_FIELD_WIDTH}}{kind}",
        f"{'what:':<{_FIELD_WIDTH}}{_WHAT.format(stamp=stamp, harness=harness)}",
        f"{'why:':<{_FIELD_WIDTH}}{bound_detail(record.get('detail'))}",
        f"{'next:':<{_FIELD_WIDTH}}{_RECOVERY}",
    )
    return "\n".join(lines) + "\n"


def render_file(path: str | Path) -> str:
    """``render`` for the breadcrumb at ``path``, or ``""``.

    BEST-EFFORT and exit-code-NEUTRAL: this runs inside a SessionStart hook
    whose exit-0 contract is inviolable, so an absent, unreadable, undecodable,
    or malformed file must yield NO output rather than an error.  The catch is
    deliberately broad (a deeply nested document raises ``RecursionError``), and
    a non-dict document is refused the same way.  ``utf-8-sig`` accepts a
    leading BOM, which a Windows-authored copy can carry.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return render(data)
