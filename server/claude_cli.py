"""Claude through Claude Code's headless mode (`claude -p`), like Harry's Chief project.

Uses the Claude Code subscription, so no API key is needed. Launched from the
Dock, the app's PATH is only /usr/bin:/bin:/usr/sbin:/sbin, so `claude` is
looked up in the configured path, the usual install locations and finally
the login shell's PATH. The child runs with that merged PATH (an
npm-installed claude needs `node`), in an empty working directory (so no
project CLAUDE.md is picked up), and without ANTHROPIC_API_KEY /
ANTHROPIC_AUTH_TOKEN (so it uses the subscription).

JSON answers are requested by pinning the schema in the system prompt:
`--json-schema` returns empty stdout for real schemas (see chief/reconcile.py).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("backchannel.claude_cli")

EXTRA_DIRS = (
    "~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin",
    "~/.bun/bin", "~/.volta/bin",
)
NVM_GLOB = "~/.nvm/versions/node/*/bin"
LOGIN_SHELL_TIMEOUT = 6.0
RECHECK_SECONDS = 300.0
MAX_CONCURRENT = 2
BASE_FLAGS = ("--no-session-persistence", "--tools", "", "--disable-slash-commands")
_BEGIN, _END = "__BACKCHANNEL_PATH_BEGIN__", "__BACKCHANNEL_PATH_END__"
_NOT_LOGGED_IN = re.compile(
    r"/login|not logged in|please log ?in|log in to claude|invalid api key|oauth token (?:has )?expired|"
    r"authentication_error|unauthorized",
    re.IGNORECASE,
)
_USAGE_LIMIT = re.compile(r"usage limit|limit reached|rate limit|credit balance", re.IGNORECASE)
JSON_INSTRUCTION = "Return ONLY valid JSON matching this JSON Schema, no prose or code fences: "


class ClaudeCLIError(RuntimeError):
    """A failed `claude -p` call; the message is meant for Harry."""


class ClaudeCLIMissing(ClaudeCLIError):
    pass


class ClaudeCLINotLoggedIn(ClaudeCLIError):
    pass


def _dedupe(dirs: list[str]) -> list[str]:
    out: list[str] = []
    for d in dirs:
        if d and d not in out:
            out.append(d)
    return out


def parse_json_text(raw: str) -> Any:
    """Strip code fences, then take the first complete JSON object (Chief's llm.call_claude_json)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, start)
            return obj
        except json.JSONDecodeError:
            pass
        end = text.rfind("}") + 1
        if end > start:
            return json.loads(text[start:end])
    raise json.JSONDecodeError("No JSON object found", text, 0)


class ClaudeCLI:
    def __init__(self, configured: str | None, workdir: Path, max_concurrent: int = MAX_CONCURRENT):
        self.configured = os.path.expanduser(configured) if configured else None
        self.workdir = Path(workdir)
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._binary: str | None = None
        self._path: str = os.environ.get("PATH", "")
        self._checked_at = 0.0
        self._login_path: str | None = None
        self._login_checked_at = 0.0

    # ------------------------------------------------------------------ finding claude

    def _login_shell_path(self) -> str:
        """$PATH as the user's login shell sets it (cached; re-probed only when claude is missing)."""
        if self._login_path is not None and time.monotonic() - self._login_checked_at < RECHECK_SECONDS:
            return self._login_path
        shell = os.environ.get("SHELL") or "/bin/zsh"
        found = ""
        try:
            result = subprocess.run(
                [shell, "-lic", f'echo {_BEGIN}; printf "%s\\n" "$PATH"; echo {_END}'],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=LOGIN_SHELL_TIMEOUT,
            )
            out = result.stdout
            if _BEGIN in out and _END in out:
                found = out.rsplit(_BEGIN, 1)[1].split(_END, 1)[0].strip()
        except (OSError, subprocess.SubprocessError) as exc:
            log.info("Couldn't read the login shell's PATH: %s", exc)
        self._login_path = found
        self._login_checked_at = time.monotonic()
        return found

    def _candidate_dirs(self) -> list[str]:
        dirs = os.environ.get("PATH", "").split(os.pathsep)
        dirs += [os.path.expanduser(d) for d in EXTRA_DIRS]
        dirs += sorted(glob.glob(os.path.expanduser(NVM_GLOB)), reverse=True)
        return _dedupe(dirs)

    def _search(self) -> tuple[str | None, str]:
        dirs = self._candidate_dirs()
        binary = None
        if self.configured and os.path.isfile(self.configured) and os.access(self.configured, os.X_OK):
            binary = self.configured
        if binary is None:
            binary = shutil.which("claude", path=os.pathsep.join(dirs))
        if binary is None:
            login = self._login_shell_path()
            if login:
                dirs = _dedupe(login.split(os.pathsep) + dirs)
                binary = shutil.which("claude", path=os.pathsep.join(dirs))
        else:
            login = self._login_path or ""
            if login:
                dirs = _dedupe(login.split(os.pathsep) + dirs)
        if binary:
            dirs = _dedupe([os.path.dirname(os.path.realpath(binary)), os.path.dirname(binary), *dirs])
        return binary, os.pathsep.join(dirs)

    def locate(self, force: bool = False) -> str | None:
        """Path to `claude`, or None. A miss is re-checked every few minutes."""
        with self._lock:
            stale = time.monotonic() - self._checked_at > RECHECK_SECONDS
            if self._checked_at and not force and (self._binary or not stale):
                if not self._binary or os.path.exists(self._binary):
                    return self._binary
            self._binary, self._path = self._search()
            self._checked_at = time.monotonic()
            if self._binary:
                log.info("Using Claude Code at %s", self._binary)
            return self._binary

    @property
    def search_path(self) -> str:
        return self._path

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = self._path or env.get("PATH", "")
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(key, None)  # use the Claude Code subscription, not an API key
        return env

    def version(self) -> str | None:
        binary = self.locate()
        if not binary:
            return None
        try:
            result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15,
                                    stdin=subprocess.DEVNULL, env=self._env())
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    # ------------------------------------------------------------------ running

    def run(self, *, user: str, system: str, model: str, timeout: float) -> str:
        """Run one `claude -p` call; returns stdout. Raises ClaudeCLIError subclasses."""
        binary = self.locate()
        if not binary:
            raise ClaudeCLIMissing("Claude Code (the `claude` command) isn't installed or couldn't be found.")
        command = [binary, "-p", "--model", model, *BASE_FLAGS, "--system-prompt", system]
        self.workdir.mkdir(parents=True, exist_ok=True)
        if not self._slots.acquire(timeout=timeout):
            raise ClaudeCLIError("Claude is busy with other requests. Try again in a moment.")
        try:
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    cwd=str(self.workdir), env=self._env(), start_new_session=True,
                )
            except FileNotFoundError as exc:
                with self._lock:
                    self._checked_at = 0.0  # look again next time
                raise ClaudeCLIMissing("The `claude` command disappeared. Reinstall Claude Code.") from exc
            try:
                stdout, stderr = process.communicate(user, timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill(process)
                raise ClaudeCLIError(f"Claude didn't answer within {int(timeout)} seconds. Try again.") from None
            log.info("claude -p --model %s took %.1fs (exit %s)", model, time.monotonic() - started, process.returncode)
        finally:
            self._slots.release()
        output = f"{stdout}\n{stderr}"
        if process.returncode != 0:
            if _NOT_LOGGED_IN.search(output):
                raise ClaudeCLINotLoggedIn(
                    "Claude Code isn't logged in. Run `claude` in Terminal once and log in, then try again.")
            if _USAGE_LIMIT.search(output):
                raise ClaudeCLIError("Claude Code says the usage limit is reached. Try again later.")
            detail = (stderr.strip() or stdout.strip()).splitlines()
            raise ClaudeCLIError(
                f"Claude Code failed (exit {process.returncode}): {detail[-1][:200] if detail else 'no output'}")
        if not stdout.strip() and _NOT_LOGGED_IN.search(stderr):
            raise ClaudeCLINotLoggedIn(
                "Claude Code isn't logged in. Run `claude` in Terminal once and log in, then try again.")
        return stdout.strip()

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        try:
            process.communicate(timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass

    def run_json(self, *, user: str, system: str, schema: dict, model: str, timeout: float) -> Any:
        prompt = f"{system}\n\n{JSON_INSTRUCTION}{json.dumps(schema, separators=(',', ':'))}"
        raw = self.run(user=user, system=prompt, model=model, timeout=timeout)
        try:
            return parse_json_text(raw)
        except json.JSONDecodeError:
            if _NOT_LOGGED_IN.search(raw):
                raise ClaudeCLINotLoggedIn(
                    "Claude Code isn't logged in. Run `claude` in Terminal once and log in, then try again.") from None
            raise ClaudeCLIError("Claude's answer wasn't valid JSON. Try again.") from None
