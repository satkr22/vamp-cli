import pathspec
from pathlib import Path


class IgnoreMatcher:
    def __init__(self, repo_root: str) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.patterns: list[str] = []
        self.spec = pathspec.PathSpec.from_lines("gitignore", self.patterns)
        
    def _rebuild(self):
        self.spec = pathspec.PathSpec.from_lines(
            "gitignore",
            self.patterns,
        )

    def add(self, patterns: list[str]):
        if not patterns:
            return

        self.patterns.extend(patterns)
        self._rebuild()
    
    def remove(self, patterns: list[str]):
        if not patterns:
            return
        del self.patterns[-len(patterns):]
        self._rebuild()
            
    def should_ignore(self, path: str) -> bool:
        relative_path = Path(path).relative_to(self.repo_root).resolve()
        return self.spec.match_file(relative_path.as_posix())
    

            
            