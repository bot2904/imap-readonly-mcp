from datetime import UTC, datetime, timedelta

from imap_readonly_mcp.connectors.imap import IMAPReadOnlyConnector
from imap_readonly_mcp.models import FolderInfo, MailboxRole, MessageSearchFilters, MessageSummary
from imap_readonly_mcp.utils.identifiers import encode_folder_path


class FakeIMAPConnector(IMAPReadOnlyConnector):
    def __init__(self) -> None:
        pass

    def list_folders(self) -> list[FolderInfo]:
        return [
            FolderInfo(
                path="INBOX",
                encoded_path=encode_folder_path("INBOX"),
                role=MailboxRole.INBOX,
                selectable=True,
            )
        ]

    def search_messages(self, filters: MessageSearchFilters) -> list[MessageSummary]:
        assert filters.folder == "INBOX"
        assert filters.offset == 0
        limit = filters.limit or 0
        base_date = datetime(2026, 1, 1, tzinfo=UTC)
        return [
            MessageSummary(
                folder_path="INBOX",
                folder_token=encode_folder_path("INBOX"),
                uid=str(index),
                subject=f"Message {index}",
                date=base_date - timedelta(minutes=index),
                resource_uri=f"mail://INBOX/{index}",
                raw_resource_uri=f"mail+raw://INBOX/{index}",
            )
            for index in range(1, min(limit, 25) + 1)
        ]


def test_search_all_folders_fetches_enough_from_folder_for_global_offset() -> None:
    connector = FakeIMAPConnector()

    results = connector.search_all_folders(MessageSearchFilters(limit=10, offset=10))

    assert [summary.uid for summary in results] == [str(index) for index in range(11, 21)]
