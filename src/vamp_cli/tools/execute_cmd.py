from vamp_cli.sandbox.terminal import Terminal

class ExecuteCommandTool:

    def __init__(self, terminal: Terminal):
        self.shell = terminal

    def execute_command(
        self,
        command: str,
        cwd: str = ".",
    ):
        return self.shell.execute(
            command=command,
            cwd=cwd,
        )