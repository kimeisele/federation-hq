"""Configuration and credential handling for the review gate.

Credentials live OUTSIDE the repository under a user configuration
directory (~/.config/federation-hq-gate/ by default). The private key is a
separate PEM file with owner-read/write-only permissions; it is never
committed, copied into the repository, or placed in a tracked .env file.

Precedence: environment variables > config file > defaults.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

APP_ID_ENV = "FEDERATION_HQ_APP_ID"
INSTALLATION_ID_ENV = "FEDERATION_HQ_INSTALLATION_ID"
KEY_PATH_ENV = "FEDERATION_HQ_PRIVATE_KEY_PATH"
CONFIG_DIR_ENV = "FEDERATION_HQ_CONFIG_DIR"

CONFIG_DIR_NAME = "federation-hq-gate"
CONFIG_FILE_NAME = "config.json"
KEY_FILE_NAME = "private-key.pem"


class GateConfigError(RuntimeError):
    """Raised for missing or unsafe configuration."""


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / CONFIG_DIR_NAME


def config_file_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


def key_file_path() -> Path:
    return config_dir() / KEY_FILE_NAME


def _read_config_file() -> dict:
    path = config_file_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise GateConfigError(f"cannot read config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GateConfigError(f"config file {path} must contain a JSON object")
    return data


def load_config() -> dict:
    """Return resolved app configuration without secrets in error text."""
    file_cfg = _read_config_file()

    app_id = os.environ.get(APP_ID_ENV) or file_cfg.get("app_id")
    installation_id = os.environ.get(INSTALLATION_ID_ENV) or file_cfg.get("installation_id")
    key_path = os.environ.get(KEY_PATH_ENV) or file_cfg.get("private_key_path") or str(key_file_path())

    missing = []
    if not app_id:
        missing.append(APP_ID_ENV)
    if not installation_id:
        missing.append(INSTALLATION_ID_ENV)
    if missing:
        raise GateConfigError(
            "missing Gate App configuration; set " + ", ".join(missing)
            + " or run `python -m federation_hq_gate setup-app`"
        )

    key = Path(key_path).expanduser()
    if not key.exists():
        raise GateConfigError(f"private key not found at {key} (set {KEY_PATH_ENV} if moved)")
    if not key.is_file():
        raise GateConfigError(f"private key path is not a file: {key}")

    return {
        "app_id": str(app_id),
        "installation_id": str(installation_id),
        "private_key_path": str(key),
    }


def check_key_permissions(key_path: Path) -> list[str]:
    """Return a list of permission problems (empty when safe).

    The private key must be readable and writable only by the owner.
    """
    problems: list[str] = []
    try:
        mode = stat.S_IMODE(key_path.stat().st_mode)
    except OSError as exc:
        return [f"cannot stat private key {key_path}: {exc}"]
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        problems.append(
            f"private key {key_path} has group/other permissions (mode {oct(mode)}); "
            "run `chmod 600`"
        )
    if not (mode & stat.S_IRUSR):
        problems.append(f"private key {key_path} is not owner-readable")
    return problems


def store_credentials(app_id: str, private_key_pem: str, webhook_secret: str | None,
                      slug: str) -> Path:
    """Persist app credentials outside the repository with safe permissions.

    Returns the private key path. Never prints the key.
    """
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    key_path = key_file_path()
    key_path.write_text(private_key_pem, encoding="utf-8")
    os.chmod(key_path, 0o600)
    cfg = {
        "app_id": app_id,
        "slug": slug,
        "private_key_path": str(key_path),
    }
    if webhook_secret:
        cfg["webhook_secret"] = webhook_secret
    config_file_path().write_text(
        json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(config_file_path(), 0o600)
    return key_path


def redact(text: str) -> str:
    """Best-effort redaction of secrets from output text."""
    lowered = text.lower()
    for marker in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "-----begin rsa private key-----"):
        idx = lowered.find(marker)
        if idx != -1:
            return text[:idx] + "<redacted>"
    return text


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1
