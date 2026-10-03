import os
import re
import json
import fnmatch
import subprocess
from typing import Any
from pathlib import Path
from vamp_cli.utils.ignore import IgnoreMatcher
from vamp_cli.workspace.workspace import Workspace
from vamp_cli.utils.error_codes import Error_codes

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
        
        res_list = _walk_dir(root=_path, ignore=self.ignore, max_depth=max_depth, res=res_list)
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
            # TODO: v2: cache file metadata / line count
            # invalidate cache after mutations
            # possibly use more efficient random-access line indexing
            
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
    
    def write_file(
        self,
        path: str,
        content: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """
        Creates a new file or overwrites an existing file.

        If overwrite=False, an existing file is not modified.
        If overwrite=True, the entire file is replaced.
        """
        _path = self.workspace.safe_path(path)

        if _path == Error_codes.PATH_SCOPE_ERROR:
            return {
                "parent": path,
                "error": Error_codes.PATH_SCOPE_ERROR.value,
            }

        file_path = Path(_path)

        if file_path.exists() and file_path.is_dir():
            return {
                "path": path,
                "success": False,
                "error": "Path is a directory, not a file.",
            }

        was_existing = file_path.exists()

        if was_existing and not overwrite:
            return {
                "path": path,
                "success": False,
                "error": (
                    "File already exists. "
                    "Set overwrite=true to replace the entire file."
                ),
            }

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            file_path.write_text(
                content,
                encoding="utf-8",
            )

            return {
                "path": path,
                "success": True,
                "created": not was_existing,
                "overwritten": was_existing,
                "bytes_written": len(content.encode("utf-8")),
            }

        except OSError as e:
            return {
                "path": path,
                "success": False,
                "error": f"Failed to write file: {str(e)}",
            }

    def apply_search_replace(
        self, 
        file_path: str, 
        search: str, 
        replace: str
    ) -> dict[str, Any]:
        
        if not os.path.exists(file_path):
            return {
                "path": file_path,
                "success": True,
                "error": f"File {file_path} not found."
            }

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
                
        # exact match
        first = content.find(search)

        if first != -1:
            second = content.find(
                search,
                first + len(search),
            )
            if second != -1:
                return {
                    "path": file_path,
                    "success": False,
                    "error": (
                        "Search string found multiple times. "
                        "Include more surrounding code to make it unique."
                    ),
                }
            new_content = (
                content[:first]
                + replace
                + content[first + len(search):]
            )
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return {
                "path": file_path,
                "success": True,
                "match_type": "exact"
            }

        # Fuzzy Whitespace Fallback if exact matche is zero
        search_words = search.split()
        if not search_words:
            return {
                "path": file_path,
                "success": True,
                "error": "Search string is empty or only contains whitespace."
            }
        
        escaped_words = [re.escape(word) for word in search_words]
        
        fuzzy_pattern = r'\s+'.join(escaped_words)
        
        matches = list(re.finditer(fuzzy_pattern, content))
        
        if len(matches) == 0:
            return {
                "path": file_path,
                "success": True,
                "error": "Search string not found. Ensure you are matching "
                "the exact characters of the existing code."
            }
                    
        if len(matches) > 1:
            return {
                "path": file_path,
                "success": True,
                "error": f"Fuzzy match found {len(matches)} times. "
                    "Include more surrounding lines to make your search block unique."
            }
                    
        # exact one match found
        match = matches[0]
        
        new_content = content[:match.start()] + replace + content[match.end():]
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            
        return {
            "path": file_path,
            "success": True,
            "match_type": "whitespace"
        }

    def file_search(
        self,
        query: str,
        path: str = ".",
        max_results: int = 100,
    ) -> dict[str, Any]:
        """
        Finds files by filename or glob pattern like "config", "*.py", "*test*", "src/**/config.*"
        Uses ripgrep's file listing, which respects .gitignore
        """
        _path = self.workspace.safe_path(path)

        if _path == Error_codes.PATH_SCOPE_ERROR:
            return {
                "parent": path,
                "error": Error_codes.PATH_SCOPE_ERROR.value,
            }

        is_glob = any(char in query for char in ["*", "?", "[", "]"])

        if is_glob:
            glob_pattern = query
        else:
            glob_pattern = f"*{query}*"

        cmd = [
            "rg",
            "--files",
        ]

        cmd.extend([
            "--glob",
            glob_pattern,
            str(_path),
        ])

        workspace_root = self.workspace.root
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return {
                "error": (
                    "ripgrep (rg) command not found. "
                    "Please install ripgrep."
                )
            }

        if result.returncode == 2:
            return {
                "error": f"rg execution failed: {result.stderr.strip()}"
            }

        matches: list[str] = []

        for line in result.stdout.splitlines():
            if len(matches) >= max_results:
                break
            try:
                rel_path = Path(line).resolve().relative_to(workspace_root).as_posix()
            except ValueError:
                rel_path = Path(line).as_posix()

            matches.append(rel_path)

        return {
            "query": query,
            "count": len(matches),
            "truncated": len(matches) >= max_results,
            "matches": matches,
        }
        

    def grep_search(
        self,
        query: str,
        path: str = ".",
        is_regex: bool = False,
        file_pattern: str | None = None,
        max_matches: int = 50,
    ) -> dict[str, Any]:
        """
        Searches for text or regex across file contents using ripgrep (rg).

        The path may be a file or directory.

        Returns matching lines with file paths and line numbers.
        """
        _path = self.workspace.safe_path(path)

        if _path == Error_codes.PATH_SCOPE_ERROR:
            return {
                "parent": path,
                "error": Error_codes.PATH_SCOPE_ERROR.value,
            }

        cmd = [
            "rg",
            "--json",
            "-n",
        ]

        if not is_regex:
            cmd.extend(["-F", "-i"])

        if file_pattern:
            cmd.extend(["--glob", file_pattern])

        cmd.extend([
            "--",
            query,
            str(_path),
        ])

        results: list[dict[str, str]] = []
        workspace_root = self.workspace.root
        try:
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as process:
                
                assert process.stdout is not None
                
                for line in process.stdout:
                    if len(results) >= max_matches:
                        process.kill()
                        break

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if event.get("type") != "match":
                        continue

                    data = event.get("data", {})

                    path_data = data.get("path", {})
                    line_number = data.get("line_number")

                    file_path = path_data.get("text")

                    if not file_path or line_number is None:
                        continue

                    try:
                        rel_path = Path(file_path).resolve().relative_to(workspace_root).as_posix()
                    except ValueError:
                        rel_path = file_path

                    content_data = data.get("lines", {})
                    content = content_data.get("text", "")

                    results.append({
                        "file": rel_path,
                        "line": line_number,
                        "content": content.rstrip()[:250],
                    })

                stderr = process.stderr.read() if process.stderr else ""
                
                # Make sure the process has finished.
                process.wait()
                
        except FileNotFoundError:
            return {
                "error": (
                    "ripgrep (rg) command not found. "
                    "Please install ripgrep."
                )
            }
        except Exception as e:
            return {
                "error": f"Failed processing ripgrep output: {str(e)}"
            }
               
        if process.returncode == 2:
            return {
                "error": f"rg execution failed: {stderr.strip()}"
            }
            
        return {
            "query": query,
            "total_matches": len(results),
            "truncated": len(results) >= max_matches,
            "matches": results,
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