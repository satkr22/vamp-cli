from dataclasses import dataclass


@dataclass
class CommandResult:
    command: str

    stdout: str
    stderr: str

    exit_code: int

    timed_out: bool = False
    truncated: bool = False