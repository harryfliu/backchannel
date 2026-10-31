"""Shared pytest fixtures: generated chat.db fixtures and app harnesses."""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fastapi.testclient import TestClient  # noqa: E402

from make_fixtures import generate  # noqa: E402
from server import fakedb  # noqa: E402
from server.app import create_app  # noqa: E402
from server.config import Settings, load_settings  # noqa: E402

BASE = "http://127.0.0.1:5556"


def settings_for(fx: Path, state: Path, **extra: str) -> Settings:
    env = {
        "BACKCHANNEL_CHAT_DB": str(fx / "chat.db"),
        "BACKCHANNEL_ADDRESSBOOK_GLOB": f"{fx}/AddressBook/Sources/*/AddressBook-v22.abcddb:{fx}/AddressBook/AddressBook-v22.abcddb",
        "BACKCHANNEL_ATTACHMENTS_ROOT": str(fx / "Attachments"),
        "BACKCHANNEL_STATE_DIR": str(state),
        "BACKCHANNEL_CACHE_DIR": str(state / "cache"),
        "BACKCHANNEL_STYLE_PROFILE": str(fx / "texting_style.json"),
        "BACKCHANNEL_CHIEF_DB": str(fx / "chief.db"),
        # Never reach a real `claude` on the test machine; CLI tests opt in with a fake one.
        "BACKCHANNEL_AI_BACKEND": "api",
        "BACKCHANNEL_SEND_MODE": "dry-run",
        "BACKCHANNEL_FAKE_DB": "1",
        "BACKCHANNEL_ME_NAME": "Harry Liu",
        "BACKCHANNEL_PORT": "5556",
        "BACKCHANNEL_DIST_DIR": str(state / "no-dist"),
    }
    env.update(extra)
    return load_settings(env=env)


class Harness:
    """A TestClient bound to an app over one fixture folder."""

    def __init__(self, fx: Path, state: Path, *, ai_client_factory: Callable[[], Any] | None = None, **env: str):
        self.fx = fx
        self.settings = settings_for(fx, state, **env)
        self.app = create_app(self.settings, start_background=False, ai_client_factory=ai_client_factory)
        self.services = self.app.state.services
        self.client = TestClient(self.app, base_url=BASE)
        self.manifest = json.loads((fx / "manifest.json").read_text())

    def __enter__(self) -> "Harness":
        self.client.__enter__()
        assert self.client.get("/api/session").status_code == 200
        return self

    def __exit__(self, *exc: Any) -> None:
        self.client.__exit__(*exc)

    # helpers ----------------------------------------------------------------

    def get(self, path: str, status: int = 200, **kw: Any) -> Any:
        response = self.client.get(path, **kw)
        assert response.status_code == status, (path, response.status_code, response.text[:500])
        return response.json() if response.headers.get("content-type", "").startswith("application/json") else response

    def post(self, path: str, body: Any = None, status: int = 200, **kw: Any) -> Any:
        response = self.client.post(path, json=body, **kw) if body is not None else self.client.post(path, **kw)
        assert response.status_code == status, (path, response.status_code, response.text[:500])
        return response.json() if response.content else None

    def guid(self, label: str) -> str:
        return self.manifest["messages"][label]

    def attachment(self, label: str, part: int = 0) -> str:
        return self.manifest["attachments"][f"{self.guid(label)}:{part}"]

    def conversations(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.get("/api/conversations")["conversations"]}

    def conversation(self, conv_id: str) -> dict:
        return self.get(f"/api/conversations/{quote(conv_id, safe='')}")

    def all_messages(self, conv_id: str, limit: int = 7) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            params = {"limit": limit, **({"before": cursor} if cursor else {})}
            page = self.get(f"/api/conversations/{quote(conv_id, safe='')}/messages", params=params)
            out = page["messages"] + out
            cursor = page["nextCursor"]
            if not cursor:
                return out

    def message(self, label: str) -> dict:
        built = self.services.builder.by_guid(self.guid(label))
        assert built is not None, label
        return built

    def db(self):
        return fakedb.connect(self.settings.chat_db)

    def events_until(self, predicate: Callable[[list[dict]], bool], timeout: float = 8.0) -> list[dict]:
        """Run watcher passes until `predicate(all events so far)` holds."""
        events: list[dict] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            events += self.services.watcher.check()
            if predicate(events):
                return events
            time.sleep(0.1)
        raise AssertionError(f"timed out; events so far: {[e['type'] for e in events]}")


@pytest.fixture(scope="session")
def fixture_src(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fixtures")
    generate(out)
    return out


@pytest.fixture(scope="session")
def ro(fixture_src, tmp_path_factory):
    """Read-only harness shared by tests that don't change chat.db or local state."""
    with Harness(fixture_src, tmp_path_factory.mktemp("state")) as harness:
        harness.services.search.sync()
        yield harness


@pytest.fixture
def make_rw(fixture_src, tmp_path):
    """Factory for harnesses over a private, writable copy of the fixtures."""
    opened: list[Harness] = []

    def make(**kw: Any) -> Harness:
        fx = tmp_path / f"fx{len(opened)}"
        shutil.copytree(fixture_src, fx)
        harness = Harness(fx, tmp_path / f"state{len(opened)}", **kw).__enter__()
        harness.services.watcher.prime()
        opened.append(harness)
        return harness

    yield make
    for harness in opened:
        harness.__exit__(None, None, None)


@pytest.fixture
def rw(make_rw) -> Harness:
    return make_rw()
