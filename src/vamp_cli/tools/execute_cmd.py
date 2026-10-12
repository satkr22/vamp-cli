from vamp_cli.sandbox.terminal import Terminal

class ExecuteCommandTool:

    def __init__(self, terminal: Terminal):
        self.terminal = terminal

    async def execute_command(
        self,
        command: str,
        cwd: str = ".",
    ):
        return await self.terminal.execute(
            command=command,
            cwd=cwd,
        )