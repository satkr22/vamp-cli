import re
from typing import Any

from vamp_cli.sandbox.base import Sandbox
from vamp_cli.sandbox.result import CommandResult


ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)

def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _truncate_output(
    text: str,
    max_chars: int = 10000,
) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    half = max_chars // 2
    omitted = len(text) - max_chars

    output = (
        f"{text[:half]}\n\n"
        f"... [Output truncated: {omitted} characters omitted] ...\n\n"
        f"{text[-half:]}"
    )

    return output, True


class Terminal:
    """
    Shell interface exposed to the agent.
    """

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def execute(
        self,
        command: str,
        cwd: str = ".",
        timeout: int = 60,
        max_output_chars: int = 12000,
    ) -> dict[str, Any]:
        """
        Execute a shell command inside the configured sandbox.

        Args:
            command:
                Shell command to execute.

            cwd:
                Working directory relative to /workspace.

            timeout:
                Maximum execution time in seconds.

            max_output_chars:
                Maximum amount of combined stdout/stderr returned
                to the model.

        Returns:
            Dictionary containing execution status and output.
        """

        if not command.strip():
            return {
                "success": False,
                "command": command,
                "exit_code": -1,
                "error": "Command cannot be empty.",
            }

        if timeout <= 0:
            return {
                "success": False,
                "command": command,
                "exit_code": -1,
                "error": "Timeout must be greater than zero.",
            }

        if max_output_chars <= 0:
            return {
                "success": False,
                "command": command,
                "exit_code": -1,
                "error": "max_output_chars must be greater than zero.",
            }

        try:
            result: CommandResult = self.sandbox.execute(
                command=command,
                cwd=cwd,
                timeout=timeout,
            )

        except ValueError as e:
            return {
                "success": False,
                "command": command,
                "exit_code": -1,
                "error": str(e),
            }

        except Exception as e:
            return {
                "success": False,
                "command": command,
                "exit_code": -1,
                "error": f"Failed to execute command: {str(e)}",
            }

        stdout = _strip_ansi(result.stdout or "")
        stderr = _strip_ansi(result.stderr or "")

        if stderr:
            combined_output = (
                stdout + "\n" + stderr
            ).strip()
        else:
            combined_output = stdout.strip()

        combined_output, truncated = _truncate_output(
            combined_output,
            max_output_chars,
        )

        if result.timed_out:
            return {
                "success": False,
                "command": command,
                "exit_code": 124,
                "error": f"Command timed out after {timeout} seconds.",
                "output": combined_output or "(No output)",
                "timed_out": True,
                "truncated": truncated,
            }

        return {
            "success": result.exit_code == 0,
            "command": command,
            "exit_code": result.exit_code,
            "output": combined_output or "(No output)",
            "timed_out": False,
            "truncated": truncated,
        }
