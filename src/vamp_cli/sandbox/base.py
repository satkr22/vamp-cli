from abc import ABC, abstractmethod
from vamp_cli.sandbox.result import CommandResult

class Sandbox(ABC):

    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def execute(
        self,
        command: str,
        cwd: str = "/workspace",
        timeout: int = 30,
        max_output_chars: int = 12000
    ) -> CommandResult:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...