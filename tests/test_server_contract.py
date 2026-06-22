from __future__ import annotations

from imap_readonly_mcp.config import MailSettings
from imap_readonly_mcp.server import create_server


def test_mail_fetch_signature_defaults():
    """
    Regression test for fnd_sig-feat-cli-command-4e6896962a-_0b9a3d01db.
    Ensures that mail_fetch parameters, specifically 'ids', have explicit Python defaults.
    """
    account_config = {
        "protocol": "imap",
        "host": "imap.example.com",
        "username": "user",
        "password": "password"
    }
    settings = MailSettings(account=account_config)
    mcp = create_server(settings)
    
    # Get the tool
    tool = mcp._tool_manager.get_tool("mail_fetch")
    func = tool.fn
    
    import inspect
    sig = inspect.signature(func)
    
    # Check that 'ids' has a default value
    assert "ids" in sig.parameters
    assert sig.parameters["ids"].default != inspect.Parameter.empty
    assert sig.parameters["ids"].default == []

    # Check other parameters mentioned in the code while we're at it
    assert sig.parameters["query"].default == ""
    assert sig.parameters["folder"].default == ""
    assert sig.parameters["unread_only"].default is False
    assert sig.parameters["limit"].default == 0
