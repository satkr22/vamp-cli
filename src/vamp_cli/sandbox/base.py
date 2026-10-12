from abc import ABC, abstractmethod
from vamp_cli.sandbox.result import CommandResult

class Sandbox(ABC):

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def execute(
        self,
        command: str,
        cwd: str = "/workspace",
        timeout: int = 30,
        max_output_chars: int = 12000
    ) -> CommandResult:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...