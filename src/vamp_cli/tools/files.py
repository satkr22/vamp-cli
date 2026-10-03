import os
import re
import fnmatch
from pathlib import Path
from typing import Any
from vamp_cli.workspace.workspace import Workspace
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

    def apply_search_replace(
        self, 
        file_path: str, 
        search: str, 
        replace: str
    ) -> str:
        if not os.path.exists(file_path):
            return f"Error: File {file_path} not found."

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        
        # Exact Match
        exact_occurrences = content.count(search)
        
        if exact_occurrences == 1:
            new_content = content.replace(search, replace)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return "Success: Code replaced (Exact match)."
            
        if exact_occurrences > 1:
            return (
                f"Error: Search string found {exact_occurrences} times. "
                "Include more surrounding lines to make it unique."
            )

        
        # Fuzzy Whitespace Fallback if exact matche is zero
        search_words = search.split()
        if not search_words:
            return "Error: Search string is empty or only contains whitespace."
        
        escaped_words = [re.escape(word) for word in search_words]
        
        fuzzy_pattern = r'\s+'.join(escaped_words)
        
        matches = list(re.finditer(fuzzy_pattern, content))
        
        if len(matches) == 0:
            return (
                "Error: Search string not found. Ensure you are matching "
                "the exact characters of the existing code."
            )
                    
        if len(matches) > 1:
            return (
                f"Error: Fuzzy match found {len(matches)} times. "
                "Include more surrounding lines to make your search block unique."
            )
                    
        # exact one match found
        match = matches[0]
        
        new_content = content[:match.start()] + replace + content[match.end():]
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            
        return "Success: Code replaced (Fuzzy whitespace match)."

    def file_search(
        self, 
        query: str, 
        path: str = ".", 
        max_results: int = 100
    ) -> dict[str, Any]:
        """
        Finds files by filename or glob pattern like *.py, *test*, src/**/config.*
        """
        _path = self.workspace.safe_path(path)
        if _path == Error_codes.PATH_SCOPE_ERROR:
            return {"parent": path, "error": Error_codes.PATH_SCOPE_ERROR.value}

        all_entries = _walk_dir(root=_path, ignore=self.ignore)
        
        matches = []
        is_glob = any(char in query for char in ["*", "?", "[", "]"])
        pattern = query if is_glob else f"*{query}*"

        for entry_path, entry_type in all_entries:
            if entry_type != "file":
                continue

            rel_path = Path(entry_path).relative_to(self.workspace.root).as_posix()
            filename = Path(entry_path).name

            # Match against either the filename or the full relative path
            if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(rel_path, pattern):
                matches.append(rel_path)
                if len(matches) >= max_results:
                    break

        return {
            "query": query,
            "count": len(matches),
            "truncated": len(matches) >= max_results,
            "matches": matches
        }
        
    
    def grep_search(
        self,
        query: str,
        path: str = ".",
        is_regex: bool = False,
        file_pattern: str | None = None,
        max_matches: int = 50
    ) -> dict[str, Any]:
        """
        Searches for text or regex across file contents in the workspace.
        Returns matching lines with file paths and line numbers.
        """
        _path = self.workspace.safe_path(path)
        if _path == Error_codes.PATH_SCOPE_ERROR:
            return {"parent": path, "error": Error_codes.PATH_SCOPE_ERROR.value}

        all_entries = _walk_dir(root=_path, ignore=self.ignore)

        if is_regex:
            try:
                pattern = re.compile(query)
            except re.error as e:
                return {"error": f"Invalid regex: {str(e)}"}
        else:
            pattern = re.compile(re.escape(query), re.IGNORECASE)

        results = []
        total_matches = 0

        for entry_path, entry_type in all_entries:
            if entry_type != "file":
                continue

            rel_path = Path(entry_path).relative_to(self.workspace.root).as_posix()

            if file_pattern and not fnmatch.fnmatch(Path(entry_path).name, file_pattern):
                continue

            # Skip binary files or unreadable encodings
            try:
                with open(entry_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, start=1):
                        if pattern.search(line):
                            results.append({
                                "file": rel_path,
                                "line": line_num,
                                "content": line.rstrip()[:250]  # Cap long lines
                            })
                            total_matches += 1
                            if total_matches >= max_matches:
                                return {
                                    "query": query,
                                    "total_matches": total_matches,
                                    "truncated": True,
                                    "matches": results
                                }
            except (OSError, PermissionError):
                continue

        return {
            "query": query,
            "total_matches": total_matches,
            "truncated": False,
            "matches": results
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


def _read_ignore_file(path: str) -> list[str] | None:
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
    else:
        return None