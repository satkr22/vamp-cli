import uuid
import posixpath
from pathlib import PurePosixPath
import subprocess
from vamp_cli.sandbox.base import Sandbox
from vamp_cli.sandbox.config import SandboxConfig
from vamp_cli.sandbox.result import CommandResult
from vamp_cli.workspace.workspace import Workspace


class DockerSandbox(Sandbox):

    def __init__(
        self,
        workspace: Workspace,
        config: SandboxConfig,
    ):
        self.workspace = str(workspace.root)
        self.config = config
        self.container_name = f"vamp-sandbox-{uuid.uuid4().hex[:8]}"

    def start(self):
        command = [
            "docker",
            "run",
            "-d",

            "--name",
            self.container_name,

            "--cpus",
            str(self.config.cpu_limit),

            "--memory",
            self.config.memory_limit,

            "--pids-limit",
            str(self.config.pids_limit),

            "-v",
            f"{self.workspace}:/workspace",

            "-w",
            "/workspace",
        ]

        if not self.config.network_enabled:
            command.extend([
                "--network",
                "none",
            ])

        command.extend([
            self.config.image,
            "sleep",
            "infinity",
        ])

        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        
        
    def execute(
        self,
        command: str,
        cwd: str = "/workspace",
        timeout: int = 30,
        max_output_chars: int = 12000
    ) -> CommandResult:

        safe_cwd = _resolve_cwd(cwd) 
        
        docker_command = [
            "docker",
            "exec",
            "-w",
            safe_cwd,
            self.container_name,
            "/bin/bash",
            "-lc",
            command,
        ]

        try:
            result = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            return CommandResult(
                command=command,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )

        except subprocess.TimeoutExpired as e:
            return CommandResult(
                command=command,
                stdout=e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout or "",
                stderr=e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr or "",
                exit_code=-1,
                timed_out=True,
            )
            
    def stop(self):
        subprocess.run(
            [
                "docker",
                "rm",
                "-f",
                self.container_name,
            ],
            capture_output=True,
            text=True,
        )
        
def _resolve_cwd(cwd: str) -> str:

    if cwd.startswith("/"):
        raise ValueError(
            "cwd must be relative to workspace"
        )

    root = PurePosixPath("/workspace")

    normalized = PurePosixPath(
        posixpath.normpath(
            str(root / cwd)
        )
    )

    if normalized != root and root not in normalized.parents:
        raise ValueError(
            "cwd escapes workspace"
        )

    return str(normalized)