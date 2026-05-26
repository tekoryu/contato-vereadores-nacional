import json
import logging
import sys
from datetime import date
from pathlib import Path


def setup_logging(log_dir: str = "logs") -> logging.Logger:
    root = logging.getLogger()

    if root.handlers:
        return logging.getLogger("pipeline")

    Path(log_dir).mkdir(exist_ok=True)
    today = date.today().isoformat()

    root.setLevel(logging.DEBUG)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%H:%M:%S"))

    file_handler = logging.FileHandler(f"{log_dir}/run-{today}.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"))

    root.addHandler(console)
    root.addHandler(file_handler)

    return logging.getLogger("pipeline")


class DecisionLogger:
    """Appends one JSON line per AI call to logs/decisions-YYYY-MM-DD.jsonl."""

    def __init__(self, log_dir: str = "logs"):
        Path(log_dir).mkdir(exist_ok=True)
        today = date.today().isoformat()
        self._path = Path(log_dir) / f"decisions-{today}.jsonl"

    def log(self, **kwargs) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kwargs, ensure_ascii=False) + "\n")
