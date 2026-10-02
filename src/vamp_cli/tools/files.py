import os
from pathlib import Path
from typing import Any
from vamp_cli.workspace import Workspace
from vamp_cli.utils.error_codes import Error_codes
from vamp_cli.utils.ignore import IgnoreMatcher

class FileTools():
    def __init__(
        self, 
        workspace: Workspace, 
        ignore: IgnoreMatcher
    ) -> None:
        self.workspace = workspace
        self.ignore = ignore
        

    def list_dir(
        self, 
        path: str, 
        max_depth: int | None = None
    ) -> dict[str, str | list[tuple[str, str]]]:
        
        res_list = []
        _path = self.workspace.safe_path(path)

        if _path == Error_codes.PATH_SCOPE_ERROR:
            return {
                "parent": path,
                "error": Error_codes.PATH_SCOPE_ERROR.value
            }
        
        res_list = _walk_dir(root=path, ignore=self.ignore, max_depth=max_depth, res=res_list)
        return {
            "parent_path": path,
            "list": res_list
        }
        

    def read_file(
        self, 
        path: str, 
        start: int | None = None, 
        end: int | None = None
    ) -> dict[str, str | int]:
        _path = self.workspace.safe_path(path)
        
        if _path == Error_codes.PATH_SCOPE_ERROR:
            return {
                "parent": path,
                "error": Error_codes.PATH_SCOPE_ERROR.value
            }
        
        if not Path(_path).is_file():
            return {
                "parent": path,
                "error": Error_codes.NOT_FILE_ERROR.value
            }
            
        MAX_RANGE = 500
        start_line = max(1, start) if start is not None else 1
        
        if end is not None:
            end_line = min(max(end, start_line), start_line + MAX_RANGE - 1)
        else:
            end_line = start_line + MAX_RANGE - 1
            
        lines = []
        try:
            total_bytes = os.path.getsize(_path) 
            total_lines = 0
            
            with open(_path, 'r', encoding='utf-8', errors='replace') as f:
                for curr_line, line in enumerate(f, start=1):
                    if start_line <= curr_line <= end_line:
                        lines.append(f"{curr_line}: {line}")
                    total_lines = curr_line 
            
            content = "".join(lines)
            
            return {
                "path": path,
                "start": start_line,
                "end": min(end_line, total_lines) if total_lines > 0 else end_line,
                "total_line": total_lines,
                "content": content,
                "file_size_bytes": total_bytes
            }
            
        except FileNotFoundError:
            return {
                "parent": path,
                "error": Error_codes.FILE_NOT_FOUND_ERROR.value
            }
        except Exception as e:
            return {
                "parent": path,
                "error": f"Failed to read file: {str(e)}"
            }

        
def _walk_dir(
    root: str, 
    ignore: IgnoreMatcher, 
    max_depth: int | None = None, 
    res: list[tuple[str, str]] | None = None
) -> list[tuple[str, str]]:
    
    if res is None:
        res = []
        
    _root = Path(root).resolve()
    stack: list[tuple[str, Path, int, list[str]|None]] = [("entry", _root, 0, None)]

    while stack:
        event, path, depth, patterns = stack.pop()
        
        if event == "exit":
            if patterns is not None:
                ignore.remove(patterns)
            continue
        
        # entry
        if path.is_symlink():
            continue
        
        local_patterns = _read_ignore_file(str(path))
        if local_patterns:
            ignore.add(local_patterns)
            
        if ignore.should_ignore(str(path)):
            if local_patterns:
                ignore.remove(local_patterns)
            continue
        
        if str(path) != root:
            res.append((str(path), "directory" if path.is_dir() else "file"))
        
        if not path.is_dir() or (max_depth is not None and depth >= max_depth):
            if local_patterns:
                ignore.remove(local_patterns)
            continue
        
        # schedule exit as after this all children of this 'path' will be pushed and popped before this scheduled exit
        stack.append(("exit", path, depth, local_patterns))
         
        children = path.iterdir()
        
        for child in children:
            stack.append(("entry", child, depth+1, None))
            
    return res


def _read_ignore_file(path: str) -> list[str]:
    pattern_list: list[str] = []
    gitignore = Path(path).resolve() / ".gitignore"
    if gitignore.is_file():
        with open(gitignore, "r") as f:
            content = f.readlines()    
            for line in content:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                else:
                    pattern_list.append(line)
    return pattern_list