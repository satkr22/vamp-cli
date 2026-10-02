import pathspec
from pathlib import Path
from vamp_cli.workspace import Workspace


class IgnoreMatcher:
    def __init__(self, repo_root: str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.patterns: list[str] = []
        
        
    def add(self, pattern: list[str]) -> None:
        if isinstance(pattern, list):
            self.patterns.extend(pattern)
        elif isinstance(pattern, str):
            self.patterns.append(pattern)
            
    def should_ignore(self, path: str) -> bool:
        relative_path = Path(path).relative_to(self.repo_root).resolve()
        spec = pathspec.PathSpec.from_lines("gitignore", self.patterns)
        return spec.match_file(relative_path.as_posix())
    

            
            