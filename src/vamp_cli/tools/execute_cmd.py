from vamp_cli.sandbox.shell import Shell

class ExecuteCommandTool:

    def __init__(self, shell: Shell):
        self.shell = shell

    def execute(
        self,
        command: str,
        cwd: str = "/workspace",
    ):
        return self.shell.execute(
            command=command,
            cwd=cwd,
        )