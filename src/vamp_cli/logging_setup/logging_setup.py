import logging, sys
from pathlib import Path

TRACE = 5

def setup(level=logging.INFO, log_dir: str | None = None):
    root = logging.getLogger()
    for h in root.handlers[:]:   
        root.removeHandler(h)
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    if not log_dir:
        return

    p = Path(log_dir)
    p.mkdir(parents=True, exist_ok=True)

    fh = logging.FileHandler(p / "vamp.log", mode="a")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # if level <= logging.DEBUG:
    #     wire = logging.FileHandler(p / "wire.log", mode="w")
    #     wire.setLevel(TRACE)
    #     wire.setFormatter(fmt)
    #     for n in ("httpx", "httpcore", "litellm", "LiteLLM"):
    #         lg = logging.getLogger(n)
    #         lg.handlers.clear()  
    #         lg.setLevel(TRACE)
    #         lg.addHandler(wire)
    #         lg.propagate = False