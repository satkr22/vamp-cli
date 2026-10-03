from vamp_cli.workspace.workspace import Workspace
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.tools.files import FileTools
from pathlib import Path

# def test_list_dir():
def test_list_dir(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").touch()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test.py").touch()

    # root = Path.cwd()
    
    ws = Workspace(root=str(tmp_path))
    ignore = IgnoreMatcher(repo_root=str(tmp_path))
    tools = FileTools(workspace=ws, ignore=ignore)
    
    entries = tools.list_dir(path=str(tmp_path))
    # entries = tools.list_dir(path=str(tmp_path), max_depth=3)

    for entry in entries:
        print(entry)
        
    # assert entries is not None
    assert len(entries) == 4