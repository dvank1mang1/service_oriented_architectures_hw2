import os
import secrets
from pathlib import Path

path = Path(".env")
if path.exists():
    print(".env already exists; left unchanged")
else:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(f"DB_PASSWORD={secrets.token_hex(24)}\nJWT_SECRET={secrets.token_hex(32)}\n")
        stream.write("APP_PORT=8081\nORDER_INTERVAL_MINUTES=1\n")
    print("created local .env with random secrets")
