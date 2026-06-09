from __future__ import annotations

from typing import Any

import pytest

from imap_readonly_mcp.connectors.graph import GraphReadOnlyConnector
from imap_readonly_mcp.exceptions import MessageNotFoundError


class FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, *, status_code: int = 200, content: bytes = b"") -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.content = content

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected GET {url}")
        return self.responses.pop(0)


def _connector(session: FakeSession) -> GraphReadOnlyConnector:
    connector = object.__new__(GraphReadOnlyConnector)
    connector._session = session
    connector._resource_path = "me"
    connector._headers = lambda **kwargs: {}
    return connector


def test_graph_fetch_message_rejects_folder_token_parent_mismatch() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "id": "message-id",
                    "parentFolderId": "folder-b",
                    "subject": "secret",
                    "body": {"content": "body"},
                }
            )
        ]
    )
    connector = _connector(session)

    with pytest.raises(MessageNotFoundError):
        connector.fetch_message("folder-a", "message-id")


def test_graph_raw_fetch_verifies_parent_before_returning_bytes() -> None:
    session = FakeSession([FakeResponse({"id": "message-id", "parentFolderId": "folder-b"})])
    connector = _connector(session)

    with pytest.raises(MessageNotFoundError):
        connector.fetch_raw_message("folder-a", "message-id")

    assert len(session.calls) == 1
    assert not session.calls[0].endswith("/$value")


def test_graph_attachment_fetch_verifies_parent_before_listing_attachments() -> None:
    session = FakeSession([FakeResponse({"id": "message-id", "parentFolderId": "folder-b"})])
    connector = _connector(session)

    with pytest.raises(MessageNotFoundError):
        connector.fetch_attachment("folder-a", "message-id", "att-id")

    assert len(session.calls) == 1
    assert not session.calls[0].endswith("/attachments")
