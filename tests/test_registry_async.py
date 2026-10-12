import json
import pytest
from vamp_cli.workspace.workspace import Workspace
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.tools.diagnostic import DiagnosticTools
from vamp_cli.tools.files import FileTools
from vamp_cli.tools.tools_classes import ReadFileTool
from vamp_cli.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_registry_call_read_file(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there\n")

    ws = Workspace(root=str(tmp_path))
    ignore = IgnoreMatcher(repo_root=str(tmp_path))
    diag = DiagnosticTools(workspace=ws)
    file_tools = FileTools(workspace=ws, ignore=ignore, diagnostic=diag)

    reg = ToolRegistry()
    reg.register(ReadFileTool(file_tools))

    raw = await reg.call("read_file", {"path": "hello.txt"})
    payload = json.loads(raw)

    assert payload["path"] == "hello.txt"
    assert "hi there" in payload["content"]