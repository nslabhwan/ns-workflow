from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _safe_id() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{time.time_ns() % 1_000_000_000:09d}-{os.getpid()}"


def write_receipt(root: Path, payload: dict[str, Any]) -> Path:
    directory = root / ".nsworkflow" / "receipts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe_id()}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
