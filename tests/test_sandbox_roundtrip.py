import pytest
import asyncio
from pathlib import Path
from vamp_cli.workspace.workspace import Workspace
from vamp_cli.sandbox.docker import DockerSandbox
from vamp_cli.sandbox.config import SandboxConfig
from vamp_cli.sandbox.terminal import Terminal

@pytest.mark.asyncio
async def test_sandbox_roundtrip(tmp_path):
    ws = Workspace(root=str(tmp_path))
    sb = DockerSandbox(ws, SandboxConfig())
    await sb.start()
    try:
        term = Terminal(sandbox=sb)
        out = await term.execute("echo hello", cwd=".")
        assert "hello" in str(out)
    finally:
        await sb.stop()


@pytest.mark.asyncio
async def test_sandbox_does_not_block_event_loop():
    from vamp_cli.workspace.workspace import Workspace
    from vamp_cli.sandbox.config import SandboxConfig
    from vamp_cli.sandbox.docker import DockerSandbox
    
    ws = Workspace(root=str(Path.cwd()))
    sb = DockerSandbox(ws, SandboxConfig())
    await sb.start()
    try:
        ticks = 0
        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        await sb.execute(command="sleep 1", cwd="src")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 1 second of container sleep → ~100 ticks if the loop is free.
        # If DockerSandbox.execute blocks, ticks stays near 0.
        assert ticks > 50, f"event loop was blocked: only {ticks} ticks"
    finally:
        await sb.stop()