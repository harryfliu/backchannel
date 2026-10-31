"""Settings and the repo-root .env file."""

from __future__ import annotations

from server.config import load_dotenv, load_settings


def test_dotenv_parsing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Backchannel secrets\n"
        "ANTHROPIC_API_KEY=\"sk-ant-test\"\n"
        "export BACKCHANNEL_PORT=5600\n"
        "QUOTED='keeps # this'\n"
        "TRAILING=value   # a comment\n"
        "EMPTY=\n"
        "not a variable\n"
        "1BAD=x\n"
        "ALREADY=from-file\n"
    )
    environ = {"ALREADY": "from-shell"}
    loaded = load_dotenv(env_file, environ)
    assert sorted(loaded) == ["ANTHROPIC_API_KEY", "BACKCHANNEL_PORT", "EMPTY", "QUOTED", "TRAILING"]
    assert environ == {"ALREADY": "from-shell", "ANTHROPIC_API_KEY": "sk-ant-test", "BACKCHANNEL_PORT": "5600",
                       "QUOTED": "keeps # this", "TRAILING": "value", "EMPTY": ""}


def test_missing_dotenv_is_fine(tmp_path):
    assert load_dotenv(tmp_path / "nope.env", {}) == []


def test_chief_db_setting(tmp_path):
    defaults = load_settings(env={})
    assert str(defaults.chief_db).endswith("Documents/claude_projects/chief/db/chief.db")
    custom = load_settings(env={"BACKCHANNEL_CHIEF_DB": str(tmp_path / "chief.db")})
    assert custom.chief_db == tmp_path / "chief.db"
