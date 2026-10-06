from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Iterable

log = logging.getLogger(__name__)


# This file is at vamp_cli/prompts/prompts.py, so .parent == vamp_cli/prompts/
PROMPTS_DIR: Path = Path(__file__).resolve().parent


class PromptNotFoundError(FileNotFoundError):
    """Raised when a prompt path cannot be resolved."""


class PromptMissingVariableError(KeyError):
    """Raised when a declared template variable is not supplied."""


@dataclass(frozen=True)
class Prompt:
    name: str
    path: Path
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def variables(self) -> list[str]:
        return list(self.metadata.get("variables", []))

    def render(self, **values: Any) -> str:
        missing = [v for v in self.variables if v not in values]
        if missing:
            raise PromptMissingVariableError(
                f"Prompt '{self.name}' missing variable(s): {', '.join(missing)}"
            )
        return Template(self.body).safe_substitute(values)


class PromptStore:
    """
    Loads prompts from `vamp_cli/prompts/`.
    Accepts both `"agent.md"` and `"prompts/agent.md"` as input 
    """

    _ROOT = PROMPTS_DIR

    def __init__(self, *, hot_reload: bool = False) -> None:
        self._hot_reload = hot_reload
        self._cache: dict[Path, Prompt] = {}
        self._mtime: dict[Path, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(self, path: str) -> str:
        """Return the raw body of the prompt. What runtime.py calls."""
        return self._get(path).body

    def load_prompt(self, path: str) -> Prompt:
        return self._get(path)

    def render(self, path: str, **values: Any) -> str:
        return self._get(path).render(**values)

    def exists(self, path: str) -> bool:
        try:
            self._resolve(path)
            return True
        except PromptNotFoundError:
            return False

    def preload(self, paths: Iterable[str]) -> None:
        """Fail-fast load of every referenced prompt. Call from bootstrap()."""
        for path in paths:
            self._get(path)
        log.info("Preloaded %d prompt(s)", len(self._cache))

    def reload(self) -> None:
        self._cache.clear()
        self._mtime.clear()

    def list_available(self) -> list[str]:
        """Relative paths of every .md file under vamp_cli/prompts/."""
        return sorted(
            p.relative_to(self._ROOT).as_posix()
            for p in self._ROOT.rglob("*.md")
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _get(self, path: str) -> Prompt:
        resolved = self._resolve(path)

        if self._hot_reload:
            mtime = resolved.stat().st_mtime
            if self._cache.get(resolved) is None or self._mtime.get(resolved) != mtime:
                self._cache[resolved] = self._parse(resolved)
                self._mtime[resolved] = mtime
            return self._cache[resolved]

        cached = self._cache.get(resolved)
        if cached is not None:
            return cached

        prompt = self._parse(resolved)
        self._cache[resolved] = prompt
        self._mtime[resolved] = resolved.stat().st_mtime
        return prompt

    def _resolve(self, path: str) -> Path:
        # Normalize: "prompts/agent.md" -> "agent.md"
        rel = path.lstrip("/\\")
        if rel.startswith("prompts/"):
            rel = rel[len("prompts/"):]

        candidate = (self._ROOT / rel).resolve()

        # Guard against ".." escaping the prompts directory.
        if self._ROOT not in candidate.parents and candidate != self._ROOT:
            raise PromptNotFoundError(
                f"Prompt path escapes vamp_cli/prompts/: {path}"
            )

        if not candidate.is_file():
            raise PromptNotFoundError(
                f"Prompt not found: {path} "
                f"(looked in {self._ROOT}; available: {self.list_available()})"
            )
        return candidate

    @staticmethod
    def _parse(path: Path) -> Prompt:
        raw = path.read_text(encoding="utf-8")
        metadata: dict[str, Any] = {}
        body = raw

        if raw.startswith("---\n"):
            end = raw.find("\n---", 4)
            if end != -1:
                metadata = _parse_front_matter(raw[4:end])
                body = raw[end + 4:].lstrip("\n")

        name = metadata.get("name") or path.stem
        return Prompt(name=name, path=path, body=body, metadata=metadata)


def _parse_front_matter(text: str) -> dict[str, Any]:
    """Minimal `key: value` + `key:\\n  - item` parser. No pyyaml dependency."""
    result: dict[str, Any] = {}
    current_list: list[str] | None = None

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_list is not None:
            current_list.append(line[4:].strip())
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if not val:
                current_list = []
                result[key] = current_list
            else:
                current_list = None
                result[key] = _coerce(val)
    return result


def _coerce(val: str) -> Any:
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    try:
        return int(val)
    except ValueError:
        return val.strip("'\"")