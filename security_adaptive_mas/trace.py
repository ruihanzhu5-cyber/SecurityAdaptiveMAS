from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class TraceLogger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, kind: str, **fields):
        event = {"time_utc": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event
