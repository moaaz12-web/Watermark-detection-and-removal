from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any


def emit_event(event_type: str, **fields: Any) -> None:
    """Emit a single structured log record without leaking exception internals."""
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "eventType": event_type,
        **fields,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=sys.stdout, flush=True)


