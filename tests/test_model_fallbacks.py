import pytest
from dataclasses import dataclass, field

@dataclass
class ModelConfig:
    fallbacks: list[str] = field(default_factory=list)

@dataclass
class AppConfig:
    models: dict[str, ModelConfig] = field(default_factory=dict)

class ConfigError(Exception):
    pass

def _check_fallback_cycles(config: AppConfig) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(model_name: str) -> None:
        if model_name in visiting:
            cycle_start_idx = path.index(model_name)
            cycle_path = path[cycle_start_idx:] + [model_name]
            path_str = " -> ".join(cycle_path)
            raise ConfigError(f"Fallback cycle detected: {path_str}")
            
        if model_name in visited:
            return

        visiting.add(model_name)
        path.append(model_name)
        
        if model_name in config.models:
            for fallback in config.models[model_name].fallbacks:
                visit(fallback)
            
        path.pop()
        visiting.remove(model_name)
        visited.add(model_name)

    for model_name in config.models:
        visit(model_name)


def test_safe_configuration():
    """Verifies that a valid, straight line fallback chain does not raise an error."""
    config = AppConfig(models={
        "gpt-4o": ModelConfig(fallbacks=["gpt-4-turbo"]),
        "gpt-4-turbo": ModelConfig(fallbacks=["claude-3-haiku"]),
        "claude-3-haiku": ModelConfig(fallbacks=[])
    })
    
    _check_fallback_cycles(config)


def test_detects_simple_cycle():
    """Verifies a direct loop between two models prints the correct path."""
    config = AppConfig(models={
        "model-a": ModelConfig(fallbacks=["model-b"]),
        "model-b": ModelConfig(fallbacks=["model-a"])
    })
    
    with pytest.raises(ConfigError) as exc_info:
        _check_fallback_cycles(config)
        
    assert str(exc_info.value) == "Fallback cycle detected: model-a -> model-b -> model-a"


def test_detects_nested_cycle_excludes_noise():
    """Verifies it isolates the cycle path even if the chain starts with safe models."""
    config = AppConfig(models={
        "entry-point": ModelConfig(fallbacks=["safe-middle"]),
        "safe-middle": ModelConfig(fallbacks=["loop-start"]),
        "loop-start": ModelConfig(fallbacks=["loop-end"]),
        "loop-end": ModelConfig(fallbacks=["loop-start"])
    })
    
    with pytest.raises(ConfigError) as exc_info:
        _check_fallback_cycles(config)
        
    assert str(exc_info.value) == "Fallback cycle detected: loop-start -> loop-end -> loop-start"
