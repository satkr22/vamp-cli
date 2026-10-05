from vamp_cli.sandbox.shell import Shell

class ExecuteCommandTool:

    def __init__(self, shell: Shell):
        self.shell = shell

    def execute_command(
        self,
        command: str,
        cwd: str = ".",
    ):
        return self.shell.execute(
            command=command,
            cwd=cwd,
        )