from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from imap_readonly_mcp.config import AccountProtocol, MailAccountConfig, MailSettings
from imap_readonly_mcp.connectors.base import ConnectorCapabilities, ReadOnlyMailConnector
from imap_readonly_mcp.exceptions import AttachmentNotFoundError, MessageNotFoundError
from imap_readonly_mcp.models import (
    AttachmentContent,
    AttachmentMetadata,
    FolderInfo,
    MailboxRole,
    MessageBody,
    MessageDetail,
    MessageSearchFilters,
    MessageSummary,
)
from imap_readonly_mcp.service import FolderAccessPolicy, MailService
from imap_readonly_mcp.utils.identifiers import encode_folder_path


def _account(
    *,
    protocol: AccountProtocol = AccountProtocol.IMAP,
    allowed_folders: list[str] | None = None,
    excluded_folders: list[str] | None = None,
) -> MailAccountConfig:
    return MailAccountConfig(
        protocol=protocol,
        host="mail.example.com",
        username="user@example.com",
        password="password",
        allowed_folders=allowed_folders,
        excluded_folders=excluded_folders,
    )


def _settings(
    tmp_path: Path,
    *,
    protocol: AccountProtocol = AccountProtocol.IMAP,
    allowed_folders: list[str] | None = None,
    excluded_folders: list[str] | None = None,
) -> MailSettings:
    return MailSettings(
        account=_account(
            protocol=protocol,
            allowed_folders=allowed_folders,
            excluded_folders=excluded_folders,
        ),
        cache_path=tmp_path / "cache.sqlite",
        default_search_limit=10,
        maximum_search_limit=50,
    )


def _summary(folder_path: str, uid: str = "1") -> MessageSummary:
    token = encode_folder_path(folder_path)
    return MessageSummary(
        folder_path=folder_path,
        folder_token=token,
        uid=uid,
        subject=f"{folder_path} {uid}",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        resource_uri=f"mail://{token}/{uid}",
        raw_resource_uri=f"mail+raw://{token}/{uid}",
    )


def _detail(folder_path: str, uid: str = "1") -> MessageDetail:
    return MessageDetail(
        **_summary(folder_path, uid).model_dump(),
        body=MessageBody(text=f"body {folder_path} {uid}"),
        attachments=[
            AttachmentMetadata(
                attachment_id="0",
                filename="hello.txt",
                content_type="text/plain",
                size=5,
                resource_uri=f"mail+attachment://{encode_folder_path(folder_path)}/{uid}/0",
            )
        ],
    )


class FakeConnector(ReadOnlyMailConnector):
    def __init__(self, config: Any, *, supports_folders: bool = True) -> None:
        super().__init__(config)
        self._supports_folders = supports_folders
        self.search_calls: list[str | None] = []
        self.fetch_calls: list[tuple[str, str]] = []
        self.raw_calls: list[tuple[str, str]] = []
        self.attachment_calls: list[tuple[str, str, int | str]] = []

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(supports_folders=self._supports_folders)

    def list_folders(self) -> list[FolderInfo]:
        return [
            FolderInfo(path="INBOX", encoded_path=encode_folder_path("INBOX"), role=MailboxRole.INBOX),
            FolderInfo(path="Secret", encoded_path=encode_folder_path("Secret")),
            FolderInfo(path="All Mail", encoded_path=encode_folder_path("All Mail"), role=MailboxRole.ARCHIVE),
        ]

    def search_messages(self, filters: MessageSearchFilters) -> list[MessageSummary]:
        self.search_calls.append(filters.folder)
        folder = filters.folder or "INBOX"
        return [_summary(folder, "1")]

    def fetch_message(self, folder_path: str, uid: str) -> MessageDetail:
        self.fetch_calls.append((folder_path, uid))
        return _detail(folder_path, uid)

    def fetch_raw_message(self, folder_path: str, uid: str) -> bytes:
        self.raw_calls.append((folder_path, uid))
        return b"raw"

    def fetch_attachment(self, folder_path: str, uid: str, attachment_index: int | str) -> AttachmentContent:
        self.attachment_calls.append((folder_path, uid, attachment_index))
        metadata = AttachmentMetadata(attachment_id=str(attachment_index), filename="hello.txt")
        return AttachmentContent(metadata=metadata, data=b"hello", mime_type="text/plain", file_name="hello.txt")


def _service(settings: MailSettings, connector: FakeConnector) -> MailService:
    service = MailService(settings)
    service._connector = connector
    return service


def test_folder_policy_exact_case_insensitive_and_excluded_override(tmp_path: Path) -> None:
    policy = FolderAccessPolicy(
        _settings(tmp_path, allowed_folders=["INBOX", "Archive"], excluded_folders=["archive"])
    )

    assert policy.is_allowed("inbox")
    assert not policy.is_allowed("Inbox/Sub")
    assert not policy.is_allowed("Archive")


def test_folder_policy_allow_all_except_exclusions_and_empty_allow_list(tmp_path: Path) -> None:
    allow_all_policy = FolderAccessPolicy(_settings(tmp_path, excluded_folders=["Spam"]))
    assert allow_all_policy.is_allowed("INBOX")
    assert not allow_all_policy.is_allowed("spam")

    empty_allow_policy = FolderAccessPolicy(_settings(tmp_path, allowed_folders=[]))
    assert not empty_allow_policy.is_allowed("INBOX")


def test_list_folders_filters_denied_folders(tmp_path: Path) -> None:
    settings = _settings(tmp_path, allowed_folders=["inbox"])
    connector = FakeConnector(settings.account)
    service = _service(settings, connector)

    folders = service.list_folders()

    assert [folder.path for folder in folders] == ["INBOX"]


def test_search_explicit_denied_folder_is_empty_and_skips_connector(tmp_path: Path) -> None:
    settings = _settings(tmp_path, allowed_folders=["INBOX"])
    connector = FakeConnector(settings.account)
    service = _service(settings, connector)

    results = service.search_messages(MessageSearchFilters(folder="Secret", limit=10))

    assert results == []
    assert connector.search_calls == []


def test_search_all_only_iterates_allowed_folders_and_ignores_denied_all_mail(tmp_path: Path) -> None:
    settings = _settings(tmp_path, excluded_folders=["All Mail", "Secret"])
    connector = FakeConnector(settings.account)
    service = _service(settings, connector)

    results = service.search_messages(MessageSearchFilters(limit=10))

    assert [summary.folder_path for summary in results] == ["INBOX"]
    assert connector.search_calls == ["INBOX"]


def test_fetch_denied_folder_never_reads_cache_or_connector(tmp_path: Path) -> None:
    settings = _settings(tmp_path, excluded_folders=["Secret"])
    connector = FakeConnector(settings.account)
    service = _service(settings, connector)
    secret_token = encode_folder_path("Secret")
    service._cache.set(secret_token, "1", _detail("Secret", "1"))

    with pytest.raises(MessageNotFoundError):
        service.fetch_message(secret_token, "1")
    with pytest.raises(MessageNotFoundError):
        service.fetch_raw_message(secret_token, "1")
    with pytest.raises(AttachmentNotFoundError):
        service.fetch_attachment(secret_token, "1", 0)

    assert connector.fetch_calls == []
    assert connector.raw_calls == []
    assert connector.attachment_calls == []


def test_cache_folder_mismatch_is_ignored_and_replaced(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    connector = FakeConnector(settings.account)
    service = _service(settings, connector)
    inbox_token = encode_folder_path("INBOX")
    service._cache.set(inbox_token, "1", _detail("Secret", "1"))

    detail = service.fetch_message(inbox_token, "1")

    assert detail.folder_path == "INBOX"
    assert connector.fetch_calls == [("INBOX", "1")]


def test_bulk_fetch_denied_folder_does_not_return_cached_detail(tmp_path: Path) -> None:
    settings = _settings(tmp_path, excluded_folders=["Secret"])
    connector = FakeConnector(settings.account)
    service = _service(settings, connector)
    secret_token = encode_folder_path("Secret")
    resource_uri = f"mail://{secret_token}/1"
    service._cache.set(secret_token, "1", _detail("Secret", "1"))

    results = service.fetch_details_bulk([(resource_uri, secret_token, "1")])

    assert results == {}
    assert connector.fetch_calls == []


def test_pop3_only_allows_inbox_virtual_folder_and_respects_exclusion(tmp_path: Path) -> None:
    settings = _settings(tmp_path, protocol=AccountProtocol.POP3)
    connector = FakeConnector(settings.account, supports_folders=False)
    service = _service(settings, connector)

    assert service.search_messages(MessageSearchFilters(limit=10))
    assert connector.search_calls == ["INBOX"]

    assert service.fetch_message(encode_folder_path("inbox"), "1").folder_path == "INBOX"

    with pytest.raises(MessageNotFoundError):
        service.fetch_message(encode_folder_path("Archive"), "1")

    excluded_settings = _settings(tmp_path / "excluded", protocol=AccountProtocol.POP3, excluded_folders=["inbox"])
    excluded_connector = FakeConnector(excluded_settings.account, supports_folders=False)
    excluded_service = _service(excluded_settings, excluded_connector)

    assert excluded_service.search_messages(MessageSearchFilters(limit=10)) == []
    assert excluded_connector.search_calls == []
