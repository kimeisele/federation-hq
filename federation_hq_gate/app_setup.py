"""One-time GitHub App setup: documented manifest browser flow with a
localhost form page and state-protected callback, plus a deterministic
manual fallback and an explicit installation-finalization step.

The manifest is submitted by the owner's browser via an HTML form POST to
GitHub's authenticated settings page — never by an unauthenticated curl
process. Credentials are stored outside the repository; the private key is
never printed or committed.
"""
from __future__ import annotations

import html
import json
import secrets
import subprocess
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import REQUIRED_PERMISSIONS
from .config import config_dir, store_credentials

MANIFEST_NAME = "federation-hq-review-gate"
MANIFEST_DESCRIPTION = (
    "Federation HQ review gate: publishes SHA-bound federation-hq/review "
    "check runs. Read-only metadata/contents/pull_requests + checks write only."
)
REDIRECT_HOST = "127.0.0.1"
_SETTINGS_APP_NEW_URL = "https://github.com/settings/apps/new"


def build_manifest(redirect_url: str) -> dict:
    return {
        "name": MANIFEST_NAME,
        "description": MANIFEST_DESCRIPTION,
        "url": "https://github.com/kimeisele/federation-hq",
        "hook_attributes": {"url": ""},
        "redirect_url": redirect_url,
        "callback_url": redirect_url,
        "public": False,
        "default_permissions": dict(REQUIRED_PERMISSIONS),
        "default_events": [],
        "request_oauth_on_install": False,
    }


def generate_state() -> str:
    """Cryptographically random, unguessable callback state."""
    return secrets.token_urlsafe(32)


class StateError(RuntimeError):
    """Callback state validation failure (missing, mismatched or replayed)."""


def validate_callback_state(got: str | None, expected: str, seen: set[str]) -> None:
    """Validate the callback ``state`` against the generated one.

    Rejects missing, mismatched and replayed states. Successful states are
    added to *seen* so a duplicate callback cannot be exchanged twice.
    """
    if not got:
        raise StateError("callback missing state parameter; refusing exchange")
    if got != expected:
        raise StateError("callback state mismatch; refusing exchange")
    if got in seen:
        raise StateError("callback state replayed; refusing duplicate exchange")
    seen.add(got)


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self.server.query = {  # type: ignore[attr-defined]
            k: v[0] for k, v in query.items()
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in query:
            self.wfile.write(
                b"<h1>Federation HQ Gate: authorization code received. You may close this tab.</h1>"
            )
        else:
            self.wfile.write(b"<h1>No code in callback.</h1>")

    def log_message(self, *args):  # noqa: D102
        return


def manifest_form_html(manifest: dict, state: str) -> str:
    """One-time local HTML page with a form posting the manifest to GitHub.

    The manifest is a hidden form field named ``manifest`` with HTML-escaped
    JSON; the ``state`` is carried in the GitHub action URL.
    """
    manifest_json = json.dumps(manifest, indent=2)
    action = f"{_SETTINGS_APP_NEW_URL}?state={urllib.parse.quote(state)}"
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Federation HQ Review Gate — create GitHub App</title></head><body>"
        "<h1>Federation HQ Review Gate</h1>"
        "<p>This page prepares the private GitHub App manifest. Continue to GitHub "
        "to confirm creation; you will be redirected back to 127.0.0.1 with a code.</p>"
        f"<form action=\"{html.escape(action, quote=True)}\" method=\"POST\">"
        f"<input type=\"hidden\" name=\"manifest\" "
        f"value=\"{html.escape(manifest_json, quote=True)}\">"
        "<button type=\"submit\">Continue to GitHub to create the App</button>"
        "</form></body></html>"
    )


def serve_manifest_form(manifest: dict, state: str) -> tuple[str, HTTPServer]:
    """Start the 127.0.0.1-only server and serve the one-time form page.

    Returns (localhost_url_to_open, server). The server must be closed by the
    caller after the callback is received.
    """
    server = HTTPServer((REDIRECT_HOST, 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_url = f"http://{REDIRECT_HOST}:{port}/callback"
    manifest["redirect_url"] = redirect_url
    manifest["callback_url"] = redirect_url
    page = manifest_form_html(manifest, state)
    server._form_page = page  # type: ignore[attr-defined]

    class _ServingHandler(_CallbackHandler):
        def do_GET(self):  # noqa: N802
            path = urllib.parse.urlparse(self.path).path
            if path == "/" or path == "/start":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(server._form_page.encode("utf-8"))  # type: ignore[attr-defined]
                return
            super().do_GET()

    server.RequestHandlerClass = _ServingHandler
    return f"http://{REDIRECT_HOST}:{port}/start", server


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


def run_manifest_flow() -> dict:
    """Run the documented manifest browser flow.

    Returns the exchanged credentials (id, pem, slug, webhook_secret). The
    installation ID is NOT set here; it is completed by
    ``setup-app --finalize-install`` after the owner installs the App.
    """
    state = generate_state()
    manifest = build_manifest("http://127.0.0.1:0/callback")
    url, server = serve_manifest_form(manifest, state)
    server.timeout = 300
    seen_states: set[str] = set()
    import time as _time
    deadline = _time.time() + 300
    try:
        print(f"1. Open this local page in a browser logged in as kimeisele:\n   {url}")
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - printing the URL is the fallback
            pass
        print("2. Click 'Continue to GitHub to create the App' on that page.")
        print("3. Confirm the app on GitHub; you will be redirected back to 127.0.0.1.")
        # Serve the form page first, then keep accepting requests until the
        # GitHub callback (code + state) arrives.
        query: dict = {}
        while _time.time() < deadline:
            server.handle_request()
            query = getattr(server, "query", {}) or {}
            if query.get("code") or query.get("state"):
                break
        try:
            validate_callback_state(query.get("state"), state, seen_states)
        except StateError as exc:
            raise RuntimeError(str(exc)) from exc
        code = query.get("code")
        if not code:
            raise RuntimeError("callback contained no code; refusing exchange")
        credentials = exchange_manifest_code(code)
    finally:
        server.server_close()
    return credentials


def cleanup_temporary_manifest() -> None:
    """Remove the temporary manifest material after the flow completes."""
    manifest_file = config_dir() / "app-manifest.json"
    try:
        if manifest_file.exists():
            manifest_file.unlink()
    except OSError:
        pass


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
    for perm, level in REQUIRED_PERMISSIONS.items():
        print(f"         {perm}: {level}")
    print("     - Where can this GitHub App be installed? Any account")
    print("     - Webhook: Active = unchecked (or set your own endpoint)")
    print("  3. Create the app; record the App ID.")
    print("  4. Generate a private key and download the PEM.")
    print("  5. Install the app on account kimeisele with 'All repositories'.")
    print("  6. Run:")
    print("       python -m federation_hq_gate setup-app --manual-store \\")
    print("         --app-id <APP_ID> --installation-id <INSTALLATION_ID> \\")
    print("         --pem-path <PEM>")
    print("  7. Validate with: python -m federation_hq_gate doctor")
    return {"manifest_path": str(manifest_file)}


def store_manual_credentials(app_id: str, installation_id: str, pem_path: str) -> None:
    """Store manually provided credentials with safe permissions."""
    pem = Path(pem_path).expanduser().read_text(encoding="utf-8")
    if "PRIVATE KEY" not in pem:
        raise RuntimeError("provided PEM file does not contain a private key")
    from .config import config_file_path, persist_installation_id
    key_path = store_credentials(app_id, pem, None, MANIFEST_NAME)
    cfg = {
        "app_id": app_id,
        "slug": MANIFEST_NAME,
        "private_key_path": str(key_path),
    }
    import os
    config_file_path().write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(config_file_path(), 0o600)
    persist_installation_id(installation_id, expected_app_id=app_id)
    print(f"Credentials stored under {config_dir()}")
    print("Installation URL: "
          "https://github.com/apps/federation-hq-review-gate/installations/new")
