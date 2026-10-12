#!/usr/bin/env python3
"""Generate local deployment .env; never commit secrets."""
from pathlib import Path
from secrets import token_hex

target = Path(".env")
if target.exists():
    raise SystemExit(".env exists: refusing to overwrite it.")
settings = {
    "POSTGRES_PASSWORD": token_hex(32),
    "JWT_SECRET_KEY": token_hex(48),
    "AUTH_ENCRYPTION_SECRET": token_hex(48),
    "PLATFORM_ADMIN_EMAIL": "CHANGE_ME@example.invalid",
    "PLATFORM_ADMIN_PASSWORD": token_hex(32),
    "DEFAULT_USER_PASSWORD": token_hex(32),
}
target.write_text("".join(f"{key}={value}\n" for key,value in settings.items()), encoding="utf-8")
target.chmod(0o600)
print("Created .env with random credentials. Change PLATFORM_ADMIN_EMAIL before deploying.")
