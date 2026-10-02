from pathlib import Path

class Workspace:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        
    def safe_path(self, target_path: str) -> Path:
        target = (self.root / target_path).resolve()
        
        try:
            target.relative_to(self.root)
        except:
            raise ValueError("Path is outside the Workspace")
        
        return target