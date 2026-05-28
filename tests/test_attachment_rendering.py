from imap_readonly_mcp.models import AttachmentMetadata
from imap_readonly_mcp.server import _attachment_metadata_value


def test_attachment_metadata_value_preserves_falsy_pydantic_values():
    metadata = AttachmentMetadata(
        attachment_id="",
        filename="",
        content_type="application/octet-stream",
        size=0,
        resource_uri="",
    )

    assert _attachment_metadata_value(metadata, "attachment_id") == ""
    assert _attachment_metadata_value(metadata, "filename") == ""
    assert _attachment_metadata_value(metadata, "size") == 0
    assert _attachment_metadata_value(metadata, "resource_uri") == ""


def test_attachment_metadata_value_supports_mapping_metadata():
    metadata = {"attachment_id": "", "filename": "", "size": 0}

    assert _attachment_metadata_value(metadata, "attachment_id") == ""
    assert _attachment_metadata_value(metadata, "filename") == ""
    assert _attachment_metadata_value(metadata, "size") == 0
    assert _attachment_metadata_value(metadata, "missing", "fallback") == "fallback"
