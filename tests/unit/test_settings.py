"""Settings: the Anthropic key is read under its plain name, everything else with the prefix."""

from pathlib import Path

import pytest

from lexinform.settings import Settings


def test_api_key_and_prefixed_values_are_read_from_an_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-test\nLEXINFORM_TERM=9\n")

    settings = Settings(_env_file=env)

    assert (settings.anthropic_api_key, settings.term) == ("sk-test", 9)


def test_api_key_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key == "sk-env"


def test_term_comes_from_the_api_unless_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEXINFORM_TERM", raising=False)

    assert Settings(_env_file=None).term is None
    assert Settings(_env_file=None, term=11).term == 11


def test_rcl_proxy_is_optional_and_read_with_the_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEXINFORM_RCL_PROXY_URL", raising=False)
    direct = Settings(_env_file=None)
    monkeypatch.setenv("LEXINFORM_RCL_PROXY_URL", "http://u:p@proxy.test:21570")

    assert direct.rcl_proxy_url == ""
    assert Settings(_env_file=None).rcl_proxy_url == "http://u:p@proxy.test:21570"


def test_telegram_settings_are_required_only_to_post() -> None:
    settings = Settings(_env_file=None, telegram_bot_token="", telegram_channel_id="")

    with pytest.raises(ValueError, match="LEXINFORM_TELEGRAM_BOT_TOKEN"):
        settings.require_telegram()


def test_a_blank_inbox_dir_means_no_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEXINFORM_INBOX_DIR", "")

    assert Settings(_env_file=None).inbox_dir is None
    assert Settings(_env_file=None, inbox_dir="/tmp/inbox").inbox_dir == Path("/tmp/inbox")
