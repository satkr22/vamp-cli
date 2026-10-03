import subprocess
from vamp_cli.workspace.workspace import Workspace

class ShellTool:

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def execute(self, command: str):
        result = subprocess.run(
            command,
            shell=True,
            cwd=self.workspace.root,
            capture_output=True,
            text=True,
            timeout=30,
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }