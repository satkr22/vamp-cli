import os
import uuid
import time
import asyncio
import platform
from pathlib import Path
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
        self.workspace = str(Path(workspace.root).resolve())
        self.config = config
        self.container_name = f"vamp-sandbox-{uuid.uuid4().hex[:8]}"

    async def start(self):
        
        await ensure_sandbox_image_exists(self.config.image)
        
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
            f"{self.workspace}:{self.workspace}",

            "-w",
            self.workspace,
        ]
        
        # Fix Linux/WSL file ownership issues
        if platform.system().lower() == "linux":
            # Avoid using root inside the container so host files don't lock up
            command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

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
        
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        _, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(
                f"Docker container startup failed:\n{stderr.decode(errors='replace')}"
            )
        
        # try:
        #     subprocess.run(
        #         command,
        #         check=True,
        #         capture_output=True,
        #         text=True,
        #     )
        # except subprocess.CalledProcessError as e:
        #     print(f"Docker container startup failed.\nError:\n{e.stderr}")
        
        
    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 30,
        max_output_chars: int = 12000
    ) -> CommandResult:

        if cwd is None:
            cwd = self.workspace
        safe_cwd = _resolve_cwd(cwd, self.workspace)
        
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-w", safe_cwd,
            self.container_name, "/bin/bash", "-lc", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # docker_command = [
        #     "docker",
        #     "exec",
        #     "-w",
        #     safe_cwd,
        #     self.container_name,
        #     "/bin/bash",
        #     "-lc",
        #     command,
        # ]

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
            
            # result = subprocess.run(
            #     docker_command,
            #     capture_output=True,
            #     text=True,
            #     timeout=timeout,
            # )

            # return CommandResult(
            #     command=command,
            #     stdout=result.stdout,
            #     stderr=result.stderr,
            #     exit_code=result.returncode,
            # )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except:
                pass
            
            try:
                stdout_b, stderr_b = await proc.communicate()
            except Exception:
                stdout_b, stderr_b = b"", b""
            
            return CommandResult(
                command=command,
                stdout=stdout_b.decode(errors="replace"),
                stderr=stderr_b.decode(errors="replace"),
                exit_code=-1,
                timed_out=True,
            )
        
        return CommandResult(
            command=command,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            exit_code=proc.returncode or 0,
        )

        # except subprocess.TimeoutExpired as e:
        #     return CommandResult(
        #         command=command,
        #         stdout=e.stdout.decode() if isinstance(e.stdout, bytes) else e.stdout or "",
        #         stderr=e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr or "",
        #         exit_code=-1,
        #         timed_out=True,
        #     )
            
    async def stop(self):
        
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", self.container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        
        # subprocess.run(
        #     [
        #         "docker",
        #         "rm",
        #         "-f",
        #         self.container_name,
        #     ],
        #     capture_output=True,
        #     text=True,
        # )
        
def _resolve_cwd(cwd: str, workspace_root: str) -> str:
    """Resolve a working directory inside the container.

    If *cwd* is an absolute path, it is returned as-is (the bind-mount
    guarantees the same path exists inside the container).  If it is
    relative, it is treated as relative to *workspace_root*.
    """
    p = Path(cwd)
    if p.is_absolute():
        # Absolute paths are used directly because the host workspace is
        # bind-mounted at its real path inside the container.
        resolved = p.resolve()
        host_root = Path(workspace_root).resolve()
        if resolved != host_root and host_root not in resolved.parents:
            raise ValueError("cwd escapes workspace")
        return str(resolved)

    # Relative path – treat as relative to workspace root.
    resolved = (Path(workspace_root) / cwd).resolve()
    host_root = Path(workspace_root).resolve()
    if resolved != host_root and host_root not in resolved.parents:
        raise ValueError("cwd escapes workspace")
    return str(resolved)


async def ensure_sandbox_image_exists(image_tag: str = SandboxConfig.image):
    """
    Checks if the specified Docker image exists locally. 
    If it doesn't, it dynamically creates a Dockerfile with essential 
    coding utilities, builds the image, and cleans up the temporary file.
    """
    docker_file = "src/vamp_cli/sandbox/Dockerfile"
    
    if await _docker_image_exists(image_tag):
        return True
    print(f"Image '{image_tag}' not found locally. Building sandbox environment...")
    print("Downloading and configuring development runtimes (Python, Node, Git)...")
    
    proc = await asyncio.create_subprocess_exec(
        "docker", "build", "-t", image_tag, "-f", docker_file, ".",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    
    assert proc.stdout is not None
    
    async for line in proc.stdout:
        # Live build progress — the user sees what's happening.
        print(line.decode(errors="replace"), end="", flush=True)
    
    await proc.wait()
    
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to auto-compile the coding sandbox image (exit {proc.returncode})."
        )

    print(f"Successfully compiled sandbox environment: {image_tag}")
    
    return True
    
    # try:
    #     # 1. Check if the image already exists in the local Docker daemon
    #     subprocess.run(
    #         ["docker", "image", "inspect", image_tag],
    #         check=True, 
    #         stdout=subprocess.DEVNULL, 
    #         stderr=subprocess.DEVNULL
    #     )
    #     # Image exists, return immediately without doing anything
    #     return True
        
    # except subprocess.CalledProcessError:
    #     # 2. Image does not exist -> Start the automated build process
    #     print(f"Image '{image_tag}' not found locally. Initiating automatic environment build...")
        
    #     try:  
    #         print("Downloading and configuring development runtimes (Python, Node, Git)...")
            
    #         # Execute the docker build command
    #         # (Remove stdout=subprocess.DEVNULL if you want the user to see the progress bar)
    #         subprocess.run(
    #             ["docker", "build", "-t", image_tag, "-f", docker_file, "."],
    #             check=True,
    #             stdout=subprocess.DEVNULL
    #         )
    #         print(f"Successfully compiled sandbox environment: {image_tag}")
    #         return True
            
    #     except subprocess.CalledProcessError as e:
    #         raise RuntimeError(f"Failed to auto-compile the coding sandbox image architecture: {e}")
        
        
async def _docker_image_exists(image_tag: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "docker", "image", "inspect", image_tag,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait() == 0