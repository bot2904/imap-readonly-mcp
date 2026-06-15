"""Connector implementations for supported mail protocols."""

from .base import ConnectorCapabilities, ReadOnlyMailConnector
from .imap import IMAPReadOnlyConnector

__all__ = [
    "ConnectorCapabilities",
    "IMAPReadOnlyConnector",
    "ReadOnlyMailConnector",
]
