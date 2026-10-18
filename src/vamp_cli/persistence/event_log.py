"""
Append-only JSONL event log for a single agent session.

File layout (per workspace):
    <user-home-dir>/.vamp/sessions/<sessionId>.jsonl   # append-only event log
    <user-home-dir>/.vamp/sessions/<sessionId>.head    # sidecar: current leaf uuid

Concurrency model:
    - One background writer task drains an asyncio.Queue in FIFO order.
    - `emit()` is fire-and-forget: enqueue and return. Display path is free.
    - `append()` enqueues and awaits the write. Use for durability-critical
      events (user_message, interrupt_request, interrupt_response, ...).
    - `parentUuid` chaining happens *inside* the writer task, so it is
      race-free even under bursts of concurrent emit() calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator



# uuid7 - time-sortable, canonical-string form
def uuid7() -> str:
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


# git branch
def _read_git_branch(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=1,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch or None
    except Exception:
        pass
    return None


# redaction hook
Redactor = Callable[[str, dict[str, Any]], dict[str, Any]]


def identity_redactor(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return data


# fsync policy
DEFAULT_FSYNC_TYPES = frozenset({
    "session_start",
    "session_end",
    "user_message",
    "mode_switch",
    "interrupt_request",
    "interrupt_response",
})


# Queue item: (event_type, data, done_future_or_None)
# _QueueItem type
_QueueItem = tuple[str, dict[str, Any], "asyncio.Future[str] | None", "str | None"]


# EventLog
class EventLog:
    """Async append-only writer for one session, backed by a writer task."""

    def __init__(
        self,
        *,
        session_id: str,
        path: Path,
        head_path: Path,
        cwd: str,
        version: str,
        git_branch: str | None,
        redactor: Redactor,
        fsync_types: frozenset[str],
    ) -> None:
        self.session_id = session_id
        self.path = path
        self.head_path = head_path
        self.cwd = cwd
        self.version = version
        self.git_branch = git_branch
        self._redactor = redactor
        self._fsync_types = fsync_types

        self._file = None
        self._last_uuid: str | None = None
        self._queue: asyncio.Queue[_QueueItem | None] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None
        self._closed = False

    # construction 
    @classmethod
    async def open(
        cls,
        session_id: str,
        *,
        workspace_root: str | os.PathLike[str],
        version: str,
        redactor: Redactor | None = None,
        fsync_on: Iterable[str] | None = None,
        cwd: str | None = None,
    ) -> "EventLog":
        sessions_dir = Path.home() / ".vamp" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        path = sessions_dir / f"{session_id}.jsonl"
        head_path = sessions_dir / f"{session_id}.head"
        cwd = cwd or str(Path.cwd())

        log = cls(
            session_id=session_id,
            path=path,
            head_path=head_path,
            cwd=cwd,
            version=version,
            git_branch=_read_git_branch(cwd),
            redactor=redactor or identity_redactor,
            fsync_types=(
                frozenset(fsync_on) if fsync_on is not None
                else DEFAULT_FSYNC_TYPES
            ),
        )

        if head_path.exists():
            log._last_uuid = head_path.read_text(encoding="utf-8").strip() or None
        elif path.exists():
            log._last_uuid = _scan_leaf_uuid(path)

        log._file = open(path, "a", encoding="utf-8", buffering=1)
        log._writer_task = asyncio.create_task(log._writer_loop())
        return log

    
    @property
    def head_uuid(self) -> str | None:
        return self._last_uuid

    def refresh_branch(self) -> None:
        self.git_branch = _read_git_branch(self.cwd)

    # writer task 
    async def _writer_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            event_type, data, done, parent_uuid = item
            try:
                event_uuid = self._write_event(event_type, data, parent_uuid)
                if done is not None and not done.done():
                    done.set_result(event_uuid)
            except Exception as e:
                if done is not None and not done.done():
                    done.set_exception(e)
            finally:
                self._queue.task_done()

    def _write_event(
        self, 
        event_type: str, 
        data: dict[str, Any],
        parent_uuid: str | None = None,
    ) -> str:
        """Synchronous - runs only inside the writer task."""
        payload = self._redactor(event_type, dict(data))

        event_uuid = uuid7()
        envelope = {
            "type": event_type,
            "uuid": event_uuid,
            "parentUuid":  parent_uuid if parent_uuid is not None else self._last_uuid,
            "sessionId": self.session_id,
            "timestamp": _iso_now(),
            "cwd": self.cwd,
            "version": self.version,
            "gitBranch": self.git_branch,
            "data": payload,
        }

        line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        self._file.write(line + "\n") # type: ignore 
        self._file.flush() # type: ignore
        if event_type in self._fsync_types:
            os.fsync(self._file.fileno()) # type: ignore

        self._last_uuid = event_uuid
        self.head_path.write_text(event_uuid, encoding="utf-8")
        return event_uuid

    # public API 
    def emit(self, event_type: str, data: dict[str, Any] | None = None, *, parent_uuid=None) -> None:
        """Fire-and-forget. Non-blocking. FIFO ordering guaranteed."""
        if self._closed:
            raise RuntimeError("EventLog is closed")
        self._queue.put_nowait((event_type, dict(data or {}), None, parent_uuid))

    async def append(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        parent_uuid=None,
    ) -> str:
        """Enqueue and wait for the durable write to complete."""
        if self._closed:
            raise RuntimeError("EventLog is closed")
        done: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._queue.put((event_type, dict(data or {}), done, parent_uuid))
        return await done

    async def flush(self) -> None:
        """Wait until every queued write has been processed."""
        await self._queue.join()

    async def close(self, reason: str = "user_exit") -> None:
        if self._closed:
            return
        try:
            await self.append("session_end", {"reason": reason})
            await self.flush()
        finally:
            self._closed = True
            await self._queue.put(None)          # sentinel
            if self._writer_task is not None:
                try:
                    await self._writer_task
                except Exception:
                    pass
            if self._file is not None:
                self._file.flush()
                try:
                    os.fsync(self._file.fileno())
                except Exception:
                    pass
                self._file.close()
                self._file = None

    # branch control (fork; no writes, no truncation)
    async def set_head(self, event_uuid: str) -> None:
        """Repoint the head. Flushes first so pending writes are durable."""
        await self.flush()
        self._last_uuid = event_uuid
        self.head_path.write_text(event_uuid, encoding="utf-8")



# helpers
def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _scan_leaf_uuid(path: Path) -> str | None:
    all_uuids: list[str] = []
    parents: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            u = event.get("uuid")
            p = event.get("parentUuid")
            if u:
                all_uuids.append(u)
            if p:
                parents.add(p)
    leaves = [u for u in all_uuids if u not in parents]
    return max(leaves) if leaves else None


def iter_events(path: Path) -> Iterator[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue