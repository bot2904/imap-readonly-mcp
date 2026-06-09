"""Mail service orchestration for MCP tools and resources."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dateparser import parse as parse_datetime

from .config import AccountProtocol, MailSettings
from .connectors import (
    GraphReadOnlyConnector,
    IMAPReadOnlyConnector,
    POP3ReadOnlyConnector,
    ReadOnlyMailConnector,
)
from .exceptions import (
    AttachmentNotFoundError,
    ConnectorNotAvailableError,
    MessageNotFoundError,
)
from .models import (
    AttachmentContent,
    FolderInfo,
    MailboxRole,
    MessageDetail,
    MessageSearchFilters,
    MessageSummary,
)
from .utils.identifiers import decode_folder_token

logger = logging.getLogger(__name__)


class FolderAccessPolicy:
    """Central, exact-match folder access policy for a configured account."""

    def __init__(self, settings: MailSettings) -> None:
        account = settings.account
        self.protocol = account.protocol
        self._allowed = _normalize_folder_set(account.allowed_folders)
        self._excluded = _normalize_folder_set(account.excluded_folders) or frozenset()

    def is_allowed(self, folder_path: str | None) -> bool:
        """Return whether a folder path may be exposed.

        Matching is exact and case-insensitive.  A configured exclusion always wins over
        an allow-list match.  POP3 has only one virtual folder, INBOX.
        """

        if not folder_path:
            return False
        normalized = _normalize_folder(folder_path)
        if self.protocol is AccountProtocol.POP3 and normalized != "inbox":
            return False
        if normalized in self._excluded:
            return False
        if self._allowed is not None:
            return normalized in self._allowed
        return True

    def filter_folders(self, folders: list[FolderInfo]) -> list[FolderInfo]:
        """Return only folders allowed by the current policy."""

        return [folder for folder in folders if self.is_allowed(folder.path)]

    def log_denied(
        self,
        folder_path: str | None,
        *,
        operation: str,
        uid: str | None = None,
        attachment_id: int | str | None = None,
        reason: str = "folder denied by policy",
    ) -> None:
        logger.info(
            "Denied mail access: protocol=%s operation=%s folder=%r uid=%r attachment_id=%r reason=%s",
            self.protocol.value,
            operation,
            folder_path,
            uid,
            attachment_id,
            reason,
        )

    def assert_allowed(
        self,
        folder_path: str | None,
        *,
        operation: str,
        uid: str | None = None,
        attachment_id: int | str | None = None,
        exc_type: type[Exception] = MessageNotFoundError,
    ) -> None:
        """Raise a public not-found style error when a folder is denied."""

        if self.is_allowed(folder_path):
            return
        self.log_denied(
            folder_path,
            operation=operation,
            uid=uid,
            attachment_id=attachment_id,
        )
        if exc_type is AttachmentNotFoundError:
            raise AttachmentNotFoundError("Attachment not found")
        raise MessageNotFoundError("Message not found")


class MailService:
    """High level façade used by the MCP server to service tool/resource requests."""

    def __init__(self, settings: MailSettings) -> None:
        self.settings = settings
        self._connector: ReadOnlyMailConnector | None = None
        self._cache = _MessageCache(settings.cache_path)
        self._folder_policy = FolderAccessPolicy(settings)
        self._executor = ThreadPoolExecutor(
            max_workers=settings.fetch_concurrency,
            thread_name_prefix="mail-fetch",
        )

    def list_folders(self) -> list[FolderInfo]:
        connector = self._get_connector()
        return self._folder_policy.filter_folders(connector.list_folders())

    def search_messages(self, filters: MessageSearchFilters) -> list[MessageSummary]:
        normalized_filters = self._normalize_filters(filters)
        if normalized_filters.folder is None:
            connector = self._get_connector()
            return self._search_all_folders(connector, normalized_filters)
        if not self._folder_policy.is_allowed(normalized_filters.folder):
            self._folder_policy.log_denied(normalized_filters.folder, operation="search_messages")
            return []
        connector = self._get_connector()
        return self._filter_summaries(
            connector.search_messages(normalized_filters),
            operation="search_messages_result",
        )

    def fetch_message(self, folder_token: str, uid: str) -> MessageDetail:
        folder_path = self._decode_and_assert_folder(folder_token, operation="fetch_message", uid=uid)
        cached = self._get_cached_allowed(folder_token, uid, folder_path)
        if cached:
            return cached
        detail = self._fetch_message_uncached(folder_token, uid, folder_path=folder_path)
        self._cache.set(folder_token, uid, detail)
        return detail

    def fetch_raw_message(self, folder_token: str, uid: str) -> bytes:
        folder_path = self._decode_and_assert_folder(folder_token, operation="fetch_raw_message", uid=uid)
        connector = self._get_connector()
        return connector.fetch_raw_message(folder_path, uid)

    def fetch_attachment(
        self,
        folder_token: str,
        uid: str,
        attachment_identifier: int | str,
    ) -> AttachmentContent:
        folder_path = self._decode_and_assert_folder(
            folder_token,
            operation="fetch_attachment",
            uid=uid,
            attachment_id=attachment_identifier,
            exc_type=AttachmentNotFoundError,
        )
        connector = self._get_connector()
        return connector.fetch_attachment(folder_path, uid, attachment_identifier)

    def fetch_details_bulk(self, requests: list[tuple[str, str, str]]) -> dict[str, MessageDetail]:
        """Fetch message details for a collection of (resource_uri, folder_token, uid)."""
        results: dict[str, MessageDetail] = {}
        missing: list[tuple[str, str, str]] = []

        for resource_uri, folder_token, uid in requests:
            try:
                folder_path = self._decode_and_assert_folder(
                    folder_token,
                    operation="fetch_details_bulk",
                    uid=uid,
                )
            except MessageNotFoundError:
                continue
            cached = self._get_cached_allowed(folder_token, uid, folder_path)
            if cached:
                results[resource_uri] = cached
            else:
                missing.append((resource_uri, folder_token, uid))

        if not missing:
            return results

        futures = {
            self._executor.submit(self._fetch_and_cache, folder_token, uid): resource_uri
            for resource_uri, folder_token, uid in missing
        }
        for future in as_completed(futures):
            resource_uri = futures[future]
            try:
                detail = future.result()
            except Exception:
                detail = None
            if detail:
                results[resource_uri] = detail
        return results

    def _get_connector(self) -> ReadOnlyMailConnector:
        if self._connector:
            return self._connector
        account = self.settings.account
        connector_cls_map: dict[AccountProtocol, type[ReadOnlyMailConnector]] = {
            AccountProtocol.IMAP: IMAPReadOnlyConnector,
            AccountProtocol.POP3: POP3ReadOnlyConnector,
            AccountProtocol.GRAPH: GraphReadOnlyConnector,
        }
        connector_cls = connector_cls_map.get(account.protocol)
        if not connector_cls:
            raise ConnectorNotAvailableError(f"No connector registered for protocol {account.protocol.value}")
        connector = connector_cls(account)
        self._connector = connector
        return connector

    def _normalize_filters(self, filters: MessageSearchFilters) -> MessageSearchFilters:
        limit = filters.limit or self.settings.default_search_limit
        limit = min(limit, self.settings.maximum_search_limit)
        since = _ensure_datetime(filters.since)
        until = _ensure_datetime(filters.until)
        offset = filters.offset or 0
        if filters.time_frame:
            frame_since, frame_until = _resolve_time_frame(filters.time_frame)
            if since is None:
                since = frame_since
            if until is None:
                until = frame_until
        folder_path = filters.folder
        # Normalize folder input: accept plain names, encoded tokens, or resource-URI-like inputs.
        if folder_path:
            # If a resource URI was mistakenly provided, extract the token portion.
            if folder_path.startswith("mail://"):
                try:
                    token = folder_path.split("://", 1)[1].split("/", 1)[0]
                    folder_path = decode_folder_token(token)
                except Exception:
                    # Fall back to original; connector may still handle or will error meaningfully.
                    pass
            else:
                # Try to interpret as an encoded token; if that fails, leave as-is (plain folder name).
                try:
                    folder_path = decode_folder_token(folder_path)
                except Exception:
                    pass
        return MessageSearchFilters(
            folder=folder_path,
            text=filters.text,
            sender=filters.sender,
            recipient=filters.recipient,
            since=since,
            until=until,
            unread_only=filters.unread_only,
            has_attachments=filters.has_attachments,
            limit=limit,
            time_frame=None,
            offset=offset,
        )

    def _search_all_folders(
        self,
        connector: ReadOnlyMailConnector,
        filters: MessageSearchFilters,
    ) -> list[MessageSummary]:
        if not connector.capabilities.supports_folders:
            inbox = "INBOX"
            if not self._folder_policy.is_allowed(inbox):
                self._folder_policy.log_denied(inbox, operation="search_all_folders")
                return []
            return self._filter_summaries(
                connector.search_messages(filters.model_copy(update={"folder": inbox})),
                operation="search_all_folders_result",
            )

        folder_infos = [folder for folder in self.list_folders() if folder.selectable]
        if not folder_infos:
            return []

        all_mail_folder = self._find_all_mail_folder(folder_infos)
        if all_mail_folder:
            return self._filter_summaries(
                connector.search_messages(
                    filters.model_copy(update={"folder": all_mail_folder, "offset": filters.offset})
                ),
                operation="search_all_folders_all_mail_result",
            )

        folders = [folder.path for folder in folder_infos]
        if not folders:
            return []

        offset = filters.offset or 0
        limit = filters.limit or self.settings.default_search_limit
        if limit <= 0:
            return []

        remaining_budget = limit + offset
        summaries: list[MessageSummary] = []

        for folder_path in folders:
            if remaining_budget <= 0:
                break
            per_folder_limit = min(remaining_budget, self.settings.maximum_search_limit)
            adjusted_filters = filters.model_copy(
                update={
                    "folder": folder_path,
                    "offset": 0,
                    "limit": per_folder_limit,
                }
            )
            folder_summaries = self._filter_summaries(
                connector.search_messages(adjusted_filters),
                operation="search_all_folders_result",
            )
            if not folder_summaries:
                continue
            summaries.extend(folder_summaries)
            remaining_budget = max(remaining_budget - len(folder_summaries), 0)

        if not summaries:
            return []

        def _sort_key(summary: MessageSummary) -> datetime:
            dt = summary.date
            if dt is None:
                return datetime.min.replace(tzinfo=UTC)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)

        summaries.sort(key=_sort_key, reverse=True)

        if offset:
            summaries = summaries[offset:]
        if limit:
            summaries = summaries[:limit]
        return summaries

    def _find_all_mail_folder(self, folders: list[FolderInfo]) -> str | None:
        scored: list[tuple[int, FolderInfo]] = []
        for folder in folders:
            if not folder.selectable:
                continue
            name = folder.path
            normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
            tokens = set(normalized.split())

            if folder.role == MailboxRole.ARCHIVE:
                scored.append((0, folder))
                continue

            if "all mail" in normalized:
                scored.append((1, folder))
                continue

            if "all messages" in normalized or "all items" in normalized:
                scored.append((2, folder))
                continue

            if "all" in tokens and ({"mail", "mails", "mailbox"} & tokens or {"messages", "items"} & tokens):
                scored.append((3, folder))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0])
        return scored[0][1].path

    def _fetch_and_cache(self, folder_token: str, uid: str) -> MessageDetail | None:
        try:
            detail = self._fetch_message_uncached(folder_token, uid)
            self._cache.set(folder_token, uid, detail)
            return detail
        except Exception:
            return None

    def _fetch_message_uncached(
        self,
        folder_token: str,
        uid: str,
        *,
        folder_path: str | None = None,
    ) -> MessageDetail:
        connector = self._get_connector()
        if folder_path is None:
            folder_path = self._decode_and_assert_folder(folder_token, operation="fetch_message", uid=uid)
        detail = connector.fetch_message(folder_path, uid)
        self._validate_detail_for_request(detail, folder_path, operation="fetch_message_result", uid=uid)
        return detail

    def _decode_and_assert_folder(
        self,
        folder_token: str,
        *,
        operation: str,
        uid: str | None = None,
        attachment_id: int | str | None = None,
        exc_type: type[Exception] = MessageNotFoundError,
    ) -> str:
        try:
            folder_path = decode_folder_token(folder_token)
        except Exception as exc:
            logger.info(
                "Denied mail access: protocol=%s operation=%s folder_token=%r uid=%r attachment_id=%r reason=invalid folder token",
                self.settings.account.protocol.value,
                operation,
                folder_token,
                uid,
                attachment_id,
            )
            if exc_type is AttachmentNotFoundError:
                raise AttachmentNotFoundError("Attachment not found") from exc
            raise MessageNotFoundError("Message not found") from exc
        self._folder_policy.assert_allowed(
            folder_path,
            operation=operation,
            uid=uid,
            attachment_id=attachment_id,
            exc_type=exc_type,
        )
        if self.settings.account.protocol is AccountProtocol.POP3:
            return "INBOX"
        return folder_path

    def _get_cached_allowed(self, folder_token: str, uid: str, folder_path: str) -> MessageDetail | None:
        cached = self._cache.get(folder_token, uid)
        if cached is None:
            return None
        try:
            self._validate_detail_for_request(cached, folder_path, operation="cache_get", uid=uid)
        except MessageNotFoundError:
            self._cache.delete(folder_token, uid)
            return None
        return cached

    def _validate_detail_for_request(
        self,
        detail: MessageDetail,
        folder_path: str,
        *,
        operation: str,
        uid: str,
    ) -> None:
        if detail.folder_path != folder_path:
            self._folder_policy.log_denied(
                detail.folder_path,
                operation=operation,
                uid=uid,
                reason=f"folder mismatch for requested folder {folder_path!r}",
            )
            raise MessageNotFoundError("Message not found")
        self._folder_policy.assert_allowed(detail.folder_path, operation=operation, uid=uid)

    def _filter_summaries(self, summaries: list[MessageSummary], *, operation: str) -> list[MessageSummary]:
        allowed: list[MessageSummary] = []
        for summary in summaries:
            if self._folder_policy.is_allowed(summary.folder_path):
                allowed.append(summary)
            else:
                self._folder_policy.log_denied(summary.folder_path, operation=operation, uid=summary.uid)
        return allowed


class _MessageCache:
    """Lightweight SQLite-backed cache keyed by folder token and UID."""

    def __init__(self, db_path: Path | None) -> None:
        if db_path is None:
            db_path = Path(os.environ.get("MAIL_CACHE_PATH", "email_cache.sqlite"))
        self._path = Path(db_path)
        self._lock = threading.Lock()
        if self._path.parent and not self._path.parent.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path)
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                create table if not exists messages (
                    folder_token text not null,
                    uid text not null,
                    updated_at real not null,
                    payload blob not null,
                    primary key(folder_token, uid)
                )
                """
            )
            con.execute("create index if not exists idx_messages_folder on messages(folder_token)")

    def get(self, folder_token: str, uid: str) -> MessageDetail | None:
        with self._lock, self._connect() as con:
            row = con.execute(
                "select payload from messages where folder_token=? and uid=?",
                (folder_token, uid),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload"].decode("utf-8"))
            return MessageDetail.model_validate(payload)
        except Exception:
            return None

    def set(self, folder_token: str, uid: str, detail: MessageDetail) -> None:
        payload = json.dumps(detail.model_dump(mode="json", by_alias=True), ensure_ascii=False)
        with self._lock, self._connect() as con:
            con.execute(
                """
                insert into messages(folder_token, uid, updated_at, payload)
                values(?, ?, strftime('%s','now'), ?)
                on conflict(folder_token, uid)
                do update set updated_at=excluded.updated_at, payload=excluded.payload
                """,
                (folder_token, uid, payload.encode("utf-8")),
            )

    def delete(self, folder_token: str, uid: str) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "delete from messages where folder_token=? and uid=?",
                (folder_token, uid),
            )


def _normalize_folder(value: str) -> str:
    return value.casefold()


def _normalize_folder_set(values: list[str] | None) -> frozenset[str] | None:
    if values is None:
        return None
    return frozenset(_normalize_folder(value) for value in values)


def _ensure_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        return None
    parsed = parse_datetime(value)
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _resolve_time_frame(label: str) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    mapping = {
        "last_hour": now - timedelta(hours=1),
        "last_24_hours": now - timedelta(hours=24),
        "last_7_days": now - timedelta(days=7),
        "last_30_days": now - timedelta(days=30),
        "last_90_days": now - timedelta(days=90),
    }
    start = mapping.get(label)
    if not start:
        raise ValueError(f"Unsupported time_frame value: {label}")
    return start, now
