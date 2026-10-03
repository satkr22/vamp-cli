from dataclasses import dataclass


@dataclass
class SandboxConfig:
    image: str = "vamp-sandbox:latest"

    cpu_limit: float = 2.0
    memory_limit: str = "2g"
    pids_limit: int = 256

    command_timeout: int = 30

    network_enabled: bool = True

    max_output_bytes: int = 1_000_000