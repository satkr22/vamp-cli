from vamp_cli.sandbox.base import Sandbox
from vamp_cli.sandbox.config import SandboxConfig
from vamp_cli.sandbox.docker import DockerSandbox
from vamp_cli.workspace.workspace import Workspace


class SandboxManager:

    def __init__(self, config: SandboxConfig):
        self.config = config
        self.sandbox = None

    def create(self, workspace: Workspace) -> Sandbox:
        if self.sandbox is not None:
            raise RuntimeError("Sandbox already exists")

        self.sandbox = DockerSandbox(
            workspace=workspace,
            config=self.config,
        )

        self.sandbox.start()

        return self.sandbox

    def destroy(self):
        if self.sandbox is None:
            return

        self.sandbox.stop()
        self.sandbox = None