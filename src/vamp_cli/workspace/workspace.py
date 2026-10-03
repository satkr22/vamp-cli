from pathlib import Path
from vamp_cli.utils.error_codes import Error_codes
from typing import Literal

class Workspace:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        
    def safe_path(self, target_path: str) -> str | Literal[Error_codes.PATH_SCOPE_ERROR]:
        target = (self.root / target_path).resolve()
        
        try:
            target.relative_to(self.root)
            return str(target)
        except:
            return Error_codes.PATH_SCOPE_ERROR