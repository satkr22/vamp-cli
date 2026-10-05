from pathlib import Path

from vamp_cli.config.loader import load_config


def main() -> None:
    # Test with the sample config. Secrets are intentionally supplied via env.
    import os

    os.environ.setdefault("DASHSCOPE_API_KEY", "test-qwen-key")
    os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

    config = load_config(Path(__file__).with_name("config.yaml"))

    assert config.models["qwen"].api_key == "test-qwen-key"
    assert config.models["qwen"].fallbacks == ["openai"]
    assert config.profiles["default"].model == "qwen"

    print("Config loader test: OK")


if __name__ == "__main__":
    main()
