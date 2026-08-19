import tomllib
from pathlib import Path


def test_project_urls_point_to_real_repository():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())
    urls = pyproject["project"]["urls"]

    assert all("github.com/example" not in url for url in urls.values())
    assert urls == {
        "Homepage": "https://github.com/AzizMarashly/imap-readonly-mcp",
        "Repository": "https://github.com/AzizMarashly/imap-readonly-mcp",
        "Documentation": "https://github.com/AzizMarashly/imap-readonly-mcp#readme",
        "Issues": "https://github.com/AzizMarashly/imap-readonly-mcp/issues",
    }


def test_mcp_dependency_uses_current_major_version():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text())

    assert "mcp>=2,<3" in pyproject["project"]["dependencies"]
