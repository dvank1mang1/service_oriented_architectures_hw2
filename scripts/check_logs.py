import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

raw = Path(sys.argv[1]).read_text()
assert "integration-test-password-123" not in raw
assert "DO_NOT_LOG_THIS" not in raw
required = {"request_id", "method", "endpoint", "status_code", "duration_ms", "user_id", "timestamp"}
entries = []
for line in raw.splitlines():
    try:
        value = json.loads(line)
    except ValueError:
        continue
    if "method" not in value:
        continue
    assert required <= value.keys(), value
    uuid.UUID(value["request_id"])
    datetime.fromisoformat(value["timestamp"])
    assert value["duration_ms"] >= 0
    if value["method"] in {"POST", "PUT", "DELETE"}:
        assert "request_body" in value
    entries.append(value)
assert entries and any(item["user_id"] for item in entries)
assert {200, 201, 204, 400, 401, 403, 404, 409, 422, 429} <= {item["status_code"] for item in entries}
assert len({item["request_id"] for item in entries}) == len(entries)
assert not any(item["status_code"] >= 500 for item in entries)
print(f"verified {len(entries)} structured request logs; no passwords leaked")
