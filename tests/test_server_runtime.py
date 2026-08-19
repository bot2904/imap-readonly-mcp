from __future__ import annotations

from typing import Any

import pytest

from imap_readonly_mcp import server as server_module


@pytest.mark.parametrize(
    ("transport", "expected"),
    [
        ("stdio", {"transport": "stdio"}),
        (
            "sse",
            {
                "transport": "sse",
                "host": "0.0.0.0",
                "port": 8765,
                "sse_path": "/events",
                "message_path": "/messages",
            },
        ),
        (
            "streamable-http",
            {
                "transport": "streamable-http",
                "host": "0.0.0.0",
                "port": 8765,
                "streamable_http_path": "/mcp-test",
            },
        ),
    ],
)
def test_main_passes_transport_settings_to_mcpserver(
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    expected: dict[str, Any],
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeServer:
        def run(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(server_module, "load_settings", lambda _: object())
    monkeypatch.setattr(server_module, "create_server", lambda _: FakeServer())
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    monkeypatch.setenv("FASTMCP_PORT", "8765")
    monkeypatch.setenv("FASTMCP_SSE_PATH", "/events")
    monkeypatch.setenv("FASTMCP_MESSAGE_PATH", "/messages")
    monkeypatch.setenv("FASTMCP_STREAMABLE_HTTP__PATH", "/mcp-test")

    server_module.main(["--config", "/dev/null", "--transport", transport])

    assert calls == [expected]
