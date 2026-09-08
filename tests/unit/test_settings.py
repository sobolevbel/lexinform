from pathlib import Path

from lexinform.settings import Settings


def test_api_key_is_read_without_prefix(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-test\nLEXINFORM_TERM=9\n")
    settings = Settings(_env_file=env)  # type: ignore[call-arg]
    assert settings.anthropic_api_key == "sk-test"
    assert settings.term == 9


def test_api_key_from_environment(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert Settings(_env_file=None).anthropic_api_key == "sk-env"  # type: ignore[call-arg]
