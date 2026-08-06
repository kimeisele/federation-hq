"""One-time GitHub App setup: manifest flow with a localhost callback, plus a
deterministic manual fallback. Credentials are stored outside the repository;
the private key is never printed or committed.
"""
from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .config import config_dir, store_credentials

MANIFEST_NAME = "federation-hq-review-gate"
MANIFEST_DESCRIPTION = (
    "Federation HQ review gate: publishes SHA-bound federation-hq/review "
    "check runs. Read-only metadata/contents/pull_requests + checks write only."
)
REDIRECT_HOST = "127.0.0.1"

_MANIFEST_PERMISSIONS = {
    "metadata": "read",
    "contents": "read",
    "pull_requests": "read",
    "checks": "write",
}


def build_manifest(redirect_url: str) -> dict:
    return {
        "name": MANIFEST_NAME,
        "description": MANIFEST_DESCRIPTION,
        "url": "https://github.com/kimeisele/federation-hq",
        "hook_attributes": {"url": ""},
        "redirect_url": redirect_url,
        "callback_url": redirect_url,
        "public": False,
        "default_permissions": dict(_MANIFEST_PERMISSIONS),
        "default_events": [],
        "request_oauth_on_install": False,
    }


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self.server.code = query.get("code", [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if self.server.code:  # type: ignore[attr-defined]
            self.wfile.write(b"<h1>Federation HQ Gate: code received. You may close this tab.</h1>")
        else:
            self.wfile.write(b"<h1>No code in callback. Check the manifest redirect.</h1>")

    def log_message(self, *args):  # noqa: D102
        return


def exchange_manifest_code(code: str) -> dict:
    """Exchange the temporary manifest code for app credentials."""
    result = subprocess.run(
        [
            "curl", "--silent", "--show-error", "--request", "POST",
            "--url", f"https://api.github.com/app-manifests/{code}/conversions",
            "--header", "Accept: application/vnd.github+json",
            "--header", "X-GitHub-Api-Version: 2022-11-28",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("manifest code exchange failed")
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not data.get("id") or not data.get("pem"):
        raise RuntimeError("manifest exchange returned no app id or private key")
    return data


def submit_manifest(manifest: dict, out_dir: Path) -> str:
    """POST the manifest to GitHub's app creation endpoint.

    Returns the URL the owner must open in a browser. The manifest JSON is
    written to a temp file to avoid shell quoting issues; it contains no
    secrets.
    """
    manifest_file = out_dir / "app-manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result = subprocess.run(
        [
            "curl", "--silent", "--show-error", "--output", "/dev/null",
            "--write-out", "%{redirect_url}",
            "--request", "POST",
            "--data", "@-",
            "--url", "https://github.com/settings/apps/new",
        ],
        input=json.dumps(manifest),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return "https://github.com/settings/apps/new"
    return result.stdout.strip() or "https://github.com/settings/apps/new"


def run_manifest_flow() -> dict:
    """Run the full browser manifest flow with a localhost callback."""
    server = HTTPServer((REDIRECT_HOST, 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_url = f"http://{REDIRECT_HOST}:{port}/callback"
    manifest = build_manifest(redirect_url)
    out_dir = config_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    url = submit_manifest(manifest, out_dir)
    print(f"1. Open this URL in a browser logged in as kimeisele and confirm the app:\n   {url}")
    print(f"2. The app manifest was written to {out_dir / 'app-manifest.json'}")
    print("3. Waiting for the manifest callback on 127.0.0.1 (never exposed)...")
    server.timeout = 300
    server.handle_request()
    code = getattr(server, "code", None)
    server.server_close()
    if not code:
        raise RuntimeError(
            "no manifest callback code received within the timeout; "
            "re-run setup-app or use the --manual fallback"
        )
    credentials = exchange_manifest_code(code)
    return credentials


def run_manual_flow() -> dict:
    """Print the exact fields the owner must enter and validate later."""
    out_dir = config_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest("http://127.0.0.1:0/callback (replace with a reachable callback)")
    manifest_file = out_dir / "app-manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Manual GitHub App creation (fallback):")
    print("  1. Open https://github.com/settings/apps/new as kimeisele")
    print("  2. Enter exactly these fields:")
    print(f"     - GitHub App name: {MANIFEST_NAME}")
    print("     - Permissions:")
    for perm, level in _MANIFEST_PERMISSIONS.items():
        print(f"         {perm}: {level}")
    print("     - Where can this GitHub App be installed? Any account")
    print("     - Webhook: Active = unchecked (or set your own endpoint)")
    print("  3. Create the app; record the App ID.")
    print("  4. Generate a private key and download the PEM.")
    print("  5. Install the app on account kimeisele with 'All repositories';")
    print("     record the Installation ID from the install URL.")
    print(f"  6. Store the PEM at {out_dir / 'private-key.pem'} (chmod 600) and set:")
    print("       FEDERATION_HQ_APP_ID, FEDERATION_HQ_INSTALLATION_ID,")
    print("       FEDERATION_HQ_PRIVATE_KEY_PATH")
    print("     or run: python -m federation_hq_gate setup-app --manual-store")
    print("  7. Validate with: python -m federation_hq_gate doctor")
    return {"manifest_path": str(manifest_file)}


def store_manual_credentials(app_id: str, installation_id: str, pem_path: str) -> None:
    """Store manually provided credentials with safe permissions."""
    pem = Path(pem_path).expanduser().read_text(encoding="utf-8")
    if "PRIVATE KEY" not in pem:
        raise RuntimeError("provided PEM file does not contain a private key")
    from .config import config_file_path
    key_path = store_credentials(app_id, pem, None, MANIFEST_NAME)
    cfg = {
        "app_id": app_id,
        "installation_id": installation_id,
        "slug": MANIFEST_NAME,
        "private_key_path": str(key_path),
    }
    config_file_path().write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    import os
    os.chmod(config_file_path(), 0o600)
    print(f"Credentials stored under {config_dir()}")
    print("Installation URL: "
          "https://github.com/apps/federation-hq-review-gate/installations/new")
    print("Install the app on account kimeisele with 'All repositories'.")
