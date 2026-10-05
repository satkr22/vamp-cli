from vamp_cli.llm.adapter import (
    ModelAdapter,
    PassThroughAdapter,
)


class AdapterRegistry:
    def __init__(
        self,
        adapters: dict[str, ModelAdapter] | None = None,
    ) -> None:
        self._adapters = {
            "default": PassThroughAdapter(),
            **(adapters or {}),
        }

    def get(self, name: str) -> ModelAdapter:
        try:
            return self._adapters[name]
        except KeyError:
            raise KeyError(
                f"Unknown model adapter '{name}'. "
                f"Available adapters: {sorted(self._adapters)}"
            ) from None