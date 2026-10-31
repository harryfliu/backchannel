"""Settings: config.json (plus an optional config.local.json) with env-var overrides.

Env vars exist mostly for dev and tests; on Harry's Mac the defaults in
config.json point at the real Messages/AddressBook locations.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "port": 5556,
    "sidebar_days": 180,
    "sidebar_max": 400,
    "chat_db": "~/Library/Messages/chat.db",
    "addressbook_glob": [
        "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb",
        "~/Library/Application Support/AddressBook/AddressBook-v22.abcddb",
    ],
    "attachments_root": "~/Library/Messages/Attachments",
    "state_dir": "~/Library/Application Support/Backchannel",
    "cache_dir": "~/Library/Caches/Backchannel",
    "style_profile_path": "~/Documents/claude_projects/imessage-checker/.texting_style.json",
    "chief_db": "~/Documents/claude_projects/chief/db/chief.db",
    "send_mode": "applescript",
    "me": {"name": None, "handles": []},
    "models": {"draft": "claude-haiku-5-5", "catchup": "claude-sonnet-5-5"},
    "models_cli": {"draft": "haiku", "catchup": "sonnet"},
    "claude_bin": None,
    "ai": {
        "backend": "auto",
        "draft_effort": "low",
        "catchup_effort": "medium",
        "timeout_seconds": 90,
        "catchup_cache_minutes": 10,
        "style_cache_hours": 72,
        "server_side_fallbacks": True,
    },
}

SEND_MODES = ("applescript", "dry-run")
AI_BACKENDS = ("auto", "claude-cli", "api")


@dataclass
class Settings:
    port: int
    sidebar_days: int
    sidebar_max: int
    chat_db: Path
    addressbook_globs: list[str]
    attachments_root: Path
    state_dir: Path
    cache_dir: Path
    style_profile_path: Path | None
    chief_db: Path | None
    send_mode: str
    fake_db: bool
    me_name: str | None
    me_handles: list[str]
    models: dict[str, str]
    models_cli: dict[str, str]
    claude_bin: str | None
    ai: dict[str, Any]
    dist_dir: Path
    dev_origins: list[str] = field(default_factory=list)

    @property
    def real_messages_dir(self) -> Path:
        return Path.home() / "Library" / "Messages"


def _expand(p: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def _merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _split_list(value: str, sep: str = ",") -> list[str]:
    return [part.strip() for part in value.split(sep) if part.strip()]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def load_settings(env: Mapping[str, str] | None = None, config_path: Path | None = None) -> Settings:
    """Build Settings from config.json, config.local.json and BACKCHANNEL_* env vars."""
    env = os.environ if env is None else env
    path = config_path or _expand(env.get("BACKCHANNEL_CONFIG", REPO_ROOT / "config.json"))
    raw = _merge(DEFAULTS, _read_json(path))
    raw = _merge(raw, _read_json(path.with_name("config.local.json")))

    globs = raw.get("addressbook_glob") or []
    if isinstance(globs, str):
        globs = [globs]
    if env.get("BACKCHANNEL_ADDRESSBOOK_GLOB"):
        # Several globs may be given, separated by os.pathsep (":").
        globs = _split_list(env["BACKCHANNEL_ADDRESSBOOK_GLOB"], os.pathsep)

    send_mode = (env.get("BACKCHANNEL_SEND_MODE") or raw.get("send_mode") or "applescript").strip().lower()
    if send_mode not in SEND_MODES:
        raise ValueError(f"BACKCHANNEL_SEND_MODE must be one of {', '.join(SEND_MODES)}, not {send_mode!r}")

    me = raw.get("me") or {}
    me_handles = list(me.get("handles") or [])
    if env.get("BACKCHANNEL_ME_HANDLES"):
        me_handles = _split_list(env["BACKCHANNEL_ME_HANDLES"])

    style = env.get("BACKCHANNEL_STYLE_PROFILE", raw.get("style_profile_path"))
    chief = env.get("BACKCHANNEL_CHIEF_DB", raw.get("chief_db"))
    ai = {**DEFAULTS["ai"], **(raw.get("ai") or {})}
    if env.get("BACKCHANNEL_AI_BACKEND"):
        ai["backend"] = env["BACKCHANNEL_AI_BACKEND"].strip().lower()
    if ai["backend"] not in AI_BACKENDS:
        raise ValueError(f"ai.backend must be one of {', '.join(AI_BACKENDS)}, not {ai['backend']!r}")

    return Settings(
        port=int(env.get("BACKCHANNEL_PORT") or raw["port"]),
        sidebar_days=int(raw["sidebar_days"]),
        sidebar_max=int(raw["sidebar_max"]),
        chat_db=_expand(env.get("BACKCHANNEL_CHAT_DB") or raw["chat_db"]),
        addressbook_globs=[str(_expand(g)) for g in globs],
        attachments_root=_expand(env.get("BACKCHANNEL_ATTACHMENTS_ROOT") or raw["attachments_root"]),
        state_dir=_expand(env.get("BACKCHANNEL_STATE_DIR") or raw["state_dir"]),
        cache_dir=_expand(env.get("BACKCHANNEL_CACHE_DIR") or raw["cache_dir"]),
        style_profile_path=_expand(style) if style else None,
        chief_db=_expand(chief) if chief else None,
        send_mode=send_mode,
        fake_db=_truthy(env.get("BACKCHANNEL_FAKE_DB")),
        me_name=env.get("BACKCHANNEL_ME_NAME") or me.get("name") or None,
        me_handles=me_handles,
        models={**DEFAULTS["models"], **(raw.get("models") or {})},
        models_cli={**DEFAULTS["models_cli"], **(raw.get("models_cli") or {})},
        claude_bin=env.get("BACKCHANNEL_CLAUDE_BIN") or raw.get("claude_bin") or None,
        ai=ai,
        dist_dir=_expand(env.get("BACKCHANNEL_DIST_DIR") or REPO_ROOT / "web" / "dist"),
        dev_origins=_split_list(env.get("BACKCHANNEL_DEV_ORIGINS", "")),
    )


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_dotenv(path: Path | None = None, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Load KEY=VALUE lines from the repo's .env into the environment.

    Supports blank lines, # comments, an optional `export ` prefix and single or
    double quotes. Variables that are already set win, so the shell (or the Mac
    app) can still override anything. Returns the names that were set.
    """
    path = path or REPO_ROOT / ".env"
    environ = os.environ if environ is None else environ
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    loaded: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.match(key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # trailing comment
        if key not in environ:
            environ[key] = value
            loaded.append(key)
    return loaded


def system_full_name() -> str | None:
    """The macOS account's full name (`id -F`), or the GECOS name elsewhere."""
    if sys.platform == "darwin" and shutil.which("id"):
        try:
            out = subprocess.run(["id", "-F"], capture_output=True, text=True, timeout=3)
            name = out.stdout.strip()
            if out.returncode == 0 and name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import pwd

        gecos = pwd.getpwnam(getpass.getuser()).pw_gecos.split(",")[0].strip()
        if gecos:
            return gecos
    except (ImportError, KeyError, OSError):
        pass
    return None
