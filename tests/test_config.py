from pathlib import Path

import pytest

from imap_readonly_mcp.config import AccountProtocol, load_settings
from imap_readonly_mcp.exceptions import ConfigurationError


def _clear_mail_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("MAIL_"):
            monkeypatch.delenv(key, raising=False)


def test_load_settings_from_environment_without_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail_env(monkeypatch)
    monkeypatch.setenv("MAIL_ACCOUNT__PROTOCOL", "imap")
    monkeypatch.setenv("MAIL_ACCOUNT__HOST", "imap.example.com")
    monkeypatch.setenv("MAIL_ACCOUNT__PORT", "993")
    monkeypatch.setenv("MAIL_ACCOUNT__USERNAME", "user@example.com")
    monkeypatch.setenv("MAIL_ACCOUNT__PASSWORD", "app-password")
    monkeypatch.setenv("MAIL_ACCOUNT__ALLOWED_FOLDERS", '["INBOX", "Archive"]')
    monkeypatch.setenv("MAIL_ACCOUNT__EXCLUDED_FOLDERS", '["Spam"]')
    monkeypatch.setenv("MAIL_FETCH_CONCURRENCY", "8")

    settings = load_settings(Path("/tmp/does-not-exist-mail-config.yaml"))

    assert settings.account.protocol is AccountProtocol.IMAP
    assert settings.account.host == "imap.example.com"
    assert settings.account.port == 993
    assert settings.account.username == "user@example.com"
    assert settings.account.password is not None
    assert settings.account.password.get_secret_value() == "app-password"
    assert settings.account.allowed_folders == ["INBOX", "Archive"]
    assert settings.account.excluded_folders == ["Spam"]
    assert settings.fetch_concurrency == 8


def test_environment_overrides_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail_env(monkeypatch)
    config_path = tmp_path / "accounts.yaml"
    config_path.write_text(
        "account:\n"
        "  protocol: imap\n"
        "  host: from-file.example.com\n"
        "  username: file-user@example.com\n"
        "  password: file-password\n"
        "default_search_limit: 10\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIL_ACCOUNT__HOST", "from-env.example.com")
    monkeypatch.setenv("MAIL_DEFAULT_SEARCH_LIMIT", "25")

    settings = load_settings(config_path)

    assert settings.account.host == "from-env.example.com"
    assert settings.account.username == "file-user@example.com"
    assert settings.default_search_limit == 25


def test_missing_config_file_without_environment_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail_env(monkeypatch)

    with pytest.raises(ConfigurationError, match="Configuration file not found"):
        load_settings(Path("/tmp/does-not-exist-mail-config.yaml"))
