from __future__ import annotations

import contextlib
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from imap_readonly_mcp.config import MailAccountConfig
from imap_readonly_mcp.connectors.pop3 import POP3ReadOnlyConnector
from imap_readonly_mcp.models import MessageSearchFilters


class FakePop3:
    def __init__(self, messages: list[bytes]):
        self.messages = messages

    def stat(self):
        return (len(self.messages), sum(len(m) for m in self.messages))

    def retr(self, which):
        index = which - 1
        if index < 0 or index >= len(self.messages):
            import poplib
            raise poplib.error_proto("-ERR no such message")
        return (b"+OK", self.messages[index].split(b"\r\n"), len(self.messages[index]))

    def quit(self):
        pass


@pytest.fixture
def pop3_connector():
    config = MailAccountConfig(
        protocol="pop3",
        host="pop.example.com",
        username="user",
        password="password",
    )
    return POP3ReadOnlyConnector(config)


def test_search_messages_pagination_with_filtering(pop3_connector):
    # Three messages: newest (3) and middle (2) don't match, oldest (1) matches.
    messages = [
        b"From: match@example.com\r\nSubject: match\r\n\r\nMatch",  # 1 (oldest)
        b"From: other@example.com\r\nSubject: other\r\n\r\nOther",  # 2
        b"From: other@example.com\r\nSubject: other\r\n\r\nOther",  # 3 (newest)
    ]
    fake_pop3 = FakePop3(messages)

    with patch.object(POP3ReadOnlyConnector, "_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = fake_pop3
        
        # Search for 'match' with limit 1. 
        # It should skip 3 and 2, find 1, and return it.
        filters = MessageSearchFilters(text="match", limit=1)
        results = pop3_connector.search_messages(filters)
        
        assert len(results) == 1
        assert results[0].uid == "1"
        assert results[0].subject == "match"


def test_search_messages_offset_with_filtering(pop3_connector):
    # Four messages: 4, 3, 2, 1. 4 and 2 match.
    messages = [
        b"From: match@example.com\r\nSubject: match 1\r\n\r\nMatch",  # 1
        b"From: match@example.com\r\nSubject: match 2\r\n\r\nMatch",  # 2
        b"From: other@example.com\r\nSubject: other 3\r\n\r\nOther",  # 3
        b"From: match@example.com\r\nSubject: match 4\r\n\r\nMatch",  # 4
    ]
    fake_pop3 = FakePop3(messages)

    with patch.object(POP3ReadOnlyConnector, "_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = fake_pop3
        
        # Search for 'match' with offset 1, limit 1.
        # Matches are 4 and 2. With offset 1, it should skip 4 and return 2.
        filters = MessageSearchFilters(text="match", offset=1, limit=1)
        results = pop3_connector.search_messages(filters)
        
        assert len(results) == 1
        assert results[0].uid == "2"
        assert results[0].subject == "match 2"
