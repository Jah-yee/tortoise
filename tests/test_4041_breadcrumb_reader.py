"""#4041 — the capture breadcrumb must be read BACK to the agent.

``~/.tortoise/capture-errors/<harness>.json`` was written by two authors and
read by nobody that could tell the user: ``session_verify`` deliberately
ignores the ``capture-failure`` kind and ``capture_spool`` only unlinks it, so
an agent whose memory had stopped being filed was never told.  The owner ruled
the agent SESSION is the primary surface (the dashboard was explicitly
rejected), so ``session-start.sh`` now renders the record to stdout, which
Claude Code injects into the session context.

The evidence standard is the one ``tests/test_4314_inert_hooks.py`` and
``tests/test_hook_run_observation.py`` set: the REAL shipped hook is executed
(bash, subprocess) and observed.  A grep of the script cannot see this
regression — it lives in control flow, and it needs the interpreter the hook
actually spawns.

Every test names the mutation that REDs it.

⛔ Two renderers exist on purpose: ``install-inert`` (pure shell — the branch is
reached BECAUSE the interpreter or module dir did not resolve) and
``capture-failure`` (Python — always written by Python, and the one detail that
needs ``redact_secrets``).  ``test_install_inert_is_rendered_with_no_python_on_
path`` is the proof that a Python-only renderer would have missed the first.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
#: Overridable so the pre-wiring RED can be demonstrated against the ORIGINAL
#: hook from git without touching the working tree:
#:   TORTOISE_SESSION_START_OVERRIDE=/tmp/orig-session-start.sh pytest …
SESSION_START = Path(os.environ.get("TORTOISE_SESSION_START_OVERRIDE")
                     or (REPO / "tortoise" / "claude-hooks"
                         / "session-start.sh"))

pytestmark = pytest.mark.skipif(
    not SESSION_START.exists(), reason="claude-hooks script not present")

#: The literal words the payload may NEVER use — the capture is spooled and
#: retried by design, so "failed" would be untrue and "run <cmd>" is the
#: imperative phrasing Claude Code's injection defences reject.
_FORBIDDEN = ("failed", "run `", "execute `", "you must", "you should")


# ── helpers ──────────────────────────────────────────────────────────────

def _crumb_path(home: Path, harness: str = "claude") -> Path:
    return home / ".tortoise" / "capture-errors" / f"{harness}.json"


def _seed_breadcrumb(home: Path, **fields) -> Path:
    path = _crumb_path(home, fields.get("harness", "claude"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, indent=2), encoding="utf-8")
    return path


def _capture_failure(**overrides) -> dict:
    record = {
        "harness": "claude",
        "detail": "Cannot reach API at https://api.example: connection refused",
        "kind": "capture-failure",
        "session_id": "imp_abc",
        "recorded_at": "2026-09-26T00:00:00Z",
    }
    record.update(overrides)
    return record


def _mock_tortoise(bindir: Path, log: Path) -> None:
    """A fake ``tortoise`` on PATH: logs argv, exits 0 for everything.

    The digest, the install probe and the replay drain must not reach the
    network in a hermetic test; the renderer under test does not go through
    this binary at all.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    mock = bindir / "tortoise"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "exit 0\n",
        encoding="utf-8")
    mock.chmod(0o755)


def _python3_shim(bindir: Path) -> Path:
    """A 3.12 ``python3`` first on PATH.

    The renderer imports ``tortoise.capture_breadcrumb``; the system
    ``/usr/bin/python3`` here is 3.9 and the repo requires 3.12, so the test
    supplies the interpreter the hook would find on a correctly-set-up host.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "python3"
    if shim.exists() or shim.is_symlink():
        shim.unlink()
    shim.symlink_to(sys.executable)
    return shim


def _shell_tools_only(bindir: Path) -> Path:
    """A PATH with the hook's shell plumbing but NO ``python3``/``tortoise``."""
    bindir.mkdir(parents=True, exist_ok=True)
    for tool in ("cat", "tr", "head", "mkdir", "date", "dirname"):
        real = shutil.which(tool)
        assert real, tool
        (bindir / tool).symlink_to(real)
    return bindir


def _run_hook(home: Path, *, path: str, src: Path | None = None,
              hook: Path | None = None) -> subprocess.CompletedProcess:
    """Drive the REAL shipped hook with a hermetic env.

    ``HOME`` is always the caller's tmp dir: the hook writes a breadcrumb and a
    hook-run record, and a test must never let them land in the developer's
    real ``$HOME``.
    """
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    env = {"HOME": str(home), "PATH": path, "TMPDIR": str(home / "tmp")}
    if src is not None:
        env["TORTOISE_SRC_DIR"] = str(src)
    return subprocess.run(
        ["/bin/bash", str(hook or SESSION_START)], input="",
        capture_output=True, text=True, env=env, timeout=120)


def _fields(stdout: str) -> dict[str, str]:
    """Split a payload into ``{label: value}`` (label without its colon)."""
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line:
            continue
        label, _, value = line.partition(":")
        out[label.strip()] = value.strip()
    return out


# ── the capture-failure path (the normal branch) ─────────────────────────

def test_a_capture_failure_breadcrumb_reaches_the_agent(tmp_path):
    """The premise of #4041: a breadcrumb left by a previous capture is RENDERED
    to the hook's stdout (which Claude Code injects), with the existing
    machine-readable ``kind`` and all three human/agent parts.

    The passing render is also PROOF the branch's code ran: the renderer module
    does not exist on ``main``, so an import resolving to the main checkout
    would raise and produce no output.

    Mutation: drop the ``_render_capture_failure_breadcrumb`` call from the
    hook, or remove the renderer — stdout is empty and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure())

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    fields = _fields(proc.stdout)
    assert fields.get("code") == "capture-failure", proc.stdout
    assert "NOT been filed" in fields.get("what", ""), proc.stdout
    assert "claude capture is affected" in fields.get("what", ""), proc.stdout
    assert "2026-09-26T00:00:00Z" in fields.get("what", ""), proc.stdout
    assert fields.get("why") == (
        "Cannot reach API at https://api.example: connection refused"), proc.stdout
    assert fields.get("next", "").startswith("Recovery:"), proc.stdout
    assert "tortoise session drain" in fields.get("next", ""), proc.stdout


def test_no_breadcrumb_means_no_output_and_exit_zero(tmp_path):
    """The exit-0 contract is inviolable: with no breadcrumb file the hook must
    emit NOTHING.  A single stray byte here is injected into every session.

    Mutation: print a header/footer unconditionally (or let the renderer emit a
    placeholder) — the byte-count assertion REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", (
        f"a no-breadcrumb session start emitted {len(proc.stdout)} bytes "
        f"({proc.stdout!r}) — it must emit none")


def test_a_stale_install_inert_breadcrumb_is_not_rendered_as_live(tmp_path):
    """An ``install-inert`` record surviving from an EARLIER inert run is stale
    once the hook resolves a module dir: rendering it would tell the agent that
    memory is not being filed while the working seam is filing it.  The record
    for the CURRENT run is rendered by the inert branch that writes it.

    Mutation: render any present record in the normal path (drop the
    ``kind == capture-failure`` gate) — the stale claim appears and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure(
        kind="install-inert",
        detail="the installed Claude session-start hook resolved nothing"))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", (
        f"a stale install-inert record was rendered as a live claim: "
        f"{proc.stdout!r}")


def test_an_unknown_kind_is_never_rendered(tmp_path):
    """No new taxonomy (#4041): only the two EXISTING ``kind`` values render. An
    unrecognised record must produce nothing rather than invent a code or
    prose-match the record.

    Mutation: render any dict, or add a fallback label — the foreign record
    produces output and this REDs."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure(kind="something-else"))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", proc.stdout


# ── the install-inert path (no interpreter reachable) ────────────────────

def test_install_inert_is_rendered_with_no_python_on_path(tmp_path):
    """The record that matters MOST for reachability: the install-inert
    breadcrumb is reached BECAUSE the interpreter or the module dir could not
    be resolved, so a Python-only renderer could never report it.  This drives
    that branch with NO ``python3`` and NO ``tortoise`` on PATH.

    Mutation: move the install-inert render behind the Python renderer (or
    otherwise require an interpreter) — nothing is printed here and this
    REDs."""
    home = tmp_path / "home"
    home.mkdir()
    hook = home / ".claude" / "hooks" / "session-start.sh"
    hook.parent.mkdir(parents=True)
    shutil.copy(SESSION_START, hook)
    hook.chmod(0o755)
    # `../..` from the installed position is $HOME — deliberately not a
    # checkout, so the module dir cannot resolve either.
    assert not (home / "tortoise").exists()

    bindir = tmp_path / "bin"
    _shell_tools_only(bindir)

    proc = _run_hook(home, path=str(bindir), hook=hook)

    assert proc.returncode == 0, proc.stderr
    assert "python3" not in os.environ.get("PATH", ""), "test PATH leaked"
    fields = _fields(proc.stdout)
    assert fields.get("code") == "install-inert", proc.stdout
    assert "NOT been filed" in fields.get("what", ""), proc.stdout
    assert "claude capture is affected" in fields.get("what", ""), proc.stdout
    assert "could not resolve a tortoise module dir" in fields.get("why", ""), \
        proc.stdout
    assert fields.get("next", "").startswith("Recovery:"), proc.stdout

    # The record the agent was told about is on disk too, so `session verify`
    # can read the same evidence.
    body = json.loads(_crumb_path(home).read_text(encoding="utf-8"))
    assert body["kind"] == "install-inert", body


# ── the payload's wording contract ───────────────────────────────────────

@pytest.mark.parametrize("kind", ["capture-failure", "install-inert"])
def test_the_payload_says_not_filed_and_is_never_imperative(tmp_path, kind):
    """Two halves of the same contract:

    * the capture is SPOOLED and retried by design, so the payload says it is
      NOT FILED — never that it "failed" (which would be untrue and would
      contradict the spool's purpose);
    * the recovery half is available ACTIONS, never commands: Claude Code's
      hook documentation warns that output framed as out-of-band system
      commands trips its prompt-injection defences, which makes Claude surface
      the text to the user instead of treating it as injected context.  This is
      vendor-mandated; the code carries the same note so it is not "fixed" into
      imperatives.

    Mutation: reword to "capture failed" / "Run `tortoise session drain` now"
    — both assertions RED."""
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    if kind == "capture-failure":
        _mock_tortoise(bindir, tmp_path / "calls.log")
        _python3_shim(bindir)
        _seed_breadcrumb(home, **_capture_failure())
        proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)
    else:
        hook = home / ".claude" / "hooks" / "session-start.sh"
        hook.parent.mkdir(parents=True)
        shutil.copy(SESSION_START, hook)
        hook.chmod(0o755)
        _shell_tools_only(bindir)
        proc = _run_hook(home, path=str(bindir), hook=hook)

    assert proc.returncode == 0, proc.stderr
    lowered = proc.stdout.lower()
    assert "not filed" in lowered, proc.stdout
    for phrase in _FORBIDDEN:
        assert phrase not in lowered, (phrase, proc.stdout)
    # The recovery half names actions, and only after the factual three parts.
    lines = proc.stdout.splitlines()
    assert lines[-1].startswith("next:     Recovery:"), proc.stdout


def test_the_detail_is_bounded_to_one_redacted_line(tmp_path):
    """An error string can carry a credential and can be a whole traceback. On
    the capture-failure path the detail is redacted through
    ``tortoise.security.redact_secrets`` and bounded to one line / ~400 chars,
    so a session start can neither leak a token nor dump a stack trace into the
    context window.

    Mutation: render the raw detail (drop ``bound_detail``) — the token
    survives, the payload gains lines, and this REDs."""
    from tortoise.capture_breadcrumb import MAX_DETAIL_CHARS

    token = "ghp_" + "a" * 36
    detail = ("first line\nsecond line\n" + token + "\n"
              + "x" * 2000)
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    _mock_tortoise(bindir, tmp_path / "calls.log")
    _python3_shim(bindir)
    _seed_breadcrumb(home, **_capture_failure(detail=detail))

    proc = _run_hook(home, path=f"{bindir}:/usr/bin:/bin", src=REPO)

    assert proc.returncode == 0, proc.stderr
    assert token not in proc.stdout, "the credential survived into the context"
    assert "[REDACTED:github_token]" in proc.stdout, proc.stdout
    lines = proc.stdout.splitlines()
    assert len(lines) == 4, proc.stdout
    why = lines[2]
    assert why.startswith("why:      "), proc.stdout
    assert len(why) <= 10 + MAX_DETAIL_CHARS + 1, (len(why), why)
    assert why.rstrip().endswith("…"), (len(why), why)


def test_the_vendor_phrasing_constraint_is_recorded_in_the_code(tmp_path):
    """The imperative ban is vendor-mandated, not stylistic.  It is pinned HERE
    because the next reader's most natural "fix" is to turn the recovery half
    into commands — and that silently costs the payload its context-injection
    path.  The note must survive in both renderers.

    Mutation: delete the constraint note from either file — this REDs."""
    hook = (REPO / "tortoise" / "claude-hooks" / "session-start.sh").read_text(
        encoding="utf-8")
    module = (REPO / "tortoise" / "capture_breadcrumb.py").read_text(
        encoding="utf-8")
    for name, text in (("session-start.sh", hook),
                       ("capture_breadcrumb.py", module)):
        assert re.search(r"prompt-injection", text), name
        assert re.search(r"imperative", text, re.IGNORECASE), name
