import ast
import json
import subprocess
from pathlib import Path
from typing import Any
from vamp_cli.workspace.workspace import Workspace
from vamp_cli.utils.error_codes import Error_codes


class DiagnosticTools:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def check_file_syntax(self, file_path: str) -> dict[str, Any]:
        """
        syntax check for newly modified files
        """
        path = self.workspace.safe_path(file_path)
        
        if path == Error_codes.PATH_SCOPE_ERROR:
            return {
                "parent": path,
                "error": Error_codes.PATH_SCOPE_ERROR.value
            }
        path = Path(path)
            
        if not path.is_file():
            return {"valid": False, "error": f"File '{file_path}' does not exist."}
            
        # Python AST Check
        if path.suffix == ".py":
            try:
                code = path.read_text(encoding="utf-8", errors="replace")
                ast.parse(code, filename=str(path))
                return {"valid": True, "diagnostics": []}
            except SyntaxError as e:
                return {
                    "valid": False,
                    "diagnostics": [{
                        "file": file_path,
                        "line": e.lineno,
                        "column": e.offset,
                        "severity": "error",
                        "message": f"SyntaxError: {e.msg}",
                        "source": "python_ast"
                    }]
                }

        # JSON Parse Check
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8", errors="replace"))
                return {"valid": True, "diagnostics": []}
            except json.JSONDecodeError as e:
                return {
                    "valid": False,
                    "diagnostics": [{
                        "file": file_path,
                        "line": e.lineno,
                        "column": e.colno,
                        "severity": "error",
                        "message": f"JSONDecodeError: {e.msg}",
                        "source": "json_parser"
                    }]
                }

        return {"valid": True, "diagnostics": []}

    def get_workspace_diagnostics(self, target_path: str = ".") -> dict[str, Any]:
        """
        Runs project-level linters if available in the environment and returns 
        structured diagnostics for the LLM.
        """
        diagnostics = []
        root = Path(self.workspace.root)

        # Python: Fast Ruff check if installed
        ruff_bin = subprocess.run(["which", "ruff"], capture_output=True, text=True).stdout.strip()
        if ruff_bin:
            cmd = ["ruff", "check", target_path, "--output-format=json"]
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
            if proc.stdout.strip():
                try:
                    ruff_errors = json.loads(proc.stdout)
                    for err in ruff_errors[:30]:  # Cap output to avoid context overflow
                        diagnostics.append({
                            "file": err.get("filename"),
                            "line": err.get("location", {}).get("row"),
                            "column": err.get("location", {}).get("column"),
                            "severity": "error",
                            "code": err.get("code"),
                            "message": err.get("message"),
                            "source": "ruff"
                        })
                except json.JSONDecodeError:
                    pass

        # TypeScript / JavaScript: Check for tsc if tsconfig exists
        if (root / "tsconfig.json").is_file():
            npx_bin = subprocess.run(["which", "npx"], capture_output=True, text=True).stdout.strip()
            if npx_bin:
                proc = subprocess.run(
                    ["npx", "tsc", "--noEmit", "--pretty", "false"],
                    cwd=root,
                    capture_output=True,
                    text=True
                )
                for line in proc.stdout.splitlines()[:20]:
                    if "error TS" in line:
                        diagnostics.append({
                            "raw": line.strip(),
                            "severity": "error",
                            "source": "tsc"
                        })

        return {
            "has_errors": len(diagnostics) > 0,
            "total_diagnostics": len(diagnostics),
            "diagnostics": diagnostics
        }