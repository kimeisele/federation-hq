"""Focused tests for the Gate setup path: manifest browser flow, state
protection, and installation finalization. No live GitHub mutations.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from federation_hq_gate import app_setup, auth, config  # noqa: E402


# ── Manifest form and state ─────────────────────────────────────────────────


def test_manifest_form_contains_manifest_field():
    state = app_setup.generate_state()
    page = app_setup.manifest_form_html(app_setup.build_manifest("http://127.0.0.1:1/cb"), state)
    assert 'name="manifest"' in page
    assert 'method="POST"' in page
    assert app_setup._SETTINGS_APP_NEW_URL in page


def test_manifest_json_html_escaped():
    manifest = app_setup.build_manifest("http://127.0.0.1:1/cb")
    page = app_setup.manifest_form_html(manifest, "s" * 32)
    manifest_json = json.dumps(manifest, indent=2)
    # The JSON must appear HTML-escaped inside the hidden field value.
    assert 'value="' + manifest_json.replace('"', "&quot;").replace("<", "&lt;") in page
    assert 'name="manifest"' in page


def test_random_state_generation():
    a = app_setup.generate_state()
    b = app_setup.generate_state()
    assert a != b
    assert len(a) >= 32


def test_state_verification_success():
    state = "expected-state"
    seen: set[str] = set()
    app_setup.validate_callback_state(state, state, seen)
    assert state in seen


def test_missing_state_rejected():
    with pytest.raises(app_setup.StateError, match="missing state"):
        app_setup.validate_callback_state(None, "expected", set())


def test_mismatched_state_rejected():
    with pytest.raises(app_setup.StateError, match="state mismatch"):
        app_setup.validate_callback_state("wrong", "expected", set())


def test_callback_replay_rejected():
    state = "expected-state"
    seen: set[str] = set()
    app_setup.validate_callback_state(state, state, seen)
    with pytest.raises(app_setup.StateError, match="replayed"):
        app_setup.validate_callback_state(state, state, seen)


def test_no_direct_curl_post_to_github_settings():
    """The manifest is never POSTed by curl to GitHub's settings page."""
    assert not hasattr(app_setup, "submit_manifest")
    source = Path(app_setup.__file__).read_text(encoding="utf-8")
    matched = False
    for line in source.splitlines():
        if "settings/apps/new" in line:
            matched = True
            assert "curl" not in line and "subprocess" not in line, line
    assert matched, "settings URL not referenced (form action missing)"


# ── Configuration: partial load and installation persistence ───────────────


@pytest.fixture()
def gate_config(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "gate-config"
    config_dir.mkdir()
    monkeypatch.setenv("FEDERATION_HQ_CONFIG_DIR", str(config_dir))
    key = config_dir / "private-key.pem"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n")
    os.chmod(key, 0o600)
    (config_dir / "config.json").write_text(json.dumps({"app_id": "12345"}))
    os.chmod(config_dir / "config.json", 0o600)
    return config_dir


def test_partial_config_loads_without_installation_id(gate_config):
    cfg = config.load_config(require_installation=False)
    assert cfg["app_id"] == "12345"
    assert "installation_id" not in cfg


def test_ordinary_runtime_config_requires_installation_id(gate_config):
    with pytest.raises(config.GateConfigError, match="FEDERATION_HQ_INSTALLATION_ID"):
        config.load_config()


def test_installation_id_persistence(gate_config):
    config.persist_installation_id("987", expected_app_id="12345")
    cfg = json.loads((gate_config / "config.json").read_text())
    assert cfg["installation_id"] == "987"


def test_config_mode_remains_0600(gate_config):
    config.persist_installation_id("987", expected_app_id="12345")
    mode = stat.S_IMODE((gate_config / "config.json").stat().st_mode)
    assert mode == 0o600


def test_persistence_refuses_switching_installation(gate_config):
    config.persist_installation_id("987", expected_app_id="12345")
    with pytest.raises(config.GateConfigError, match="refusing to silently switch"):
        config.persist_installation_id("555", expected_app_id="12345")


def test_finalization_idempotent(gate_config):
    config.persist_installation_id("987", expected_app_id="12345")
    config.persist_installation_id("987", expected_app_id="12345")  # same: no-op
    cfg = json.loads((gate_config / "config.json").read_text())
    assert cfg["installation_id"] == "987"


def test_persistence_refuses_wrong_app(gate_config):
    with pytest.raises(config.GateConfigError, match="different app id"):
        config.persist_installation_id("987", expected_app_id="999")


# ── Installation finalization ───────────────────────────────────────────────


class _FakeCompleted:
    def __init__(self, payload, status=200):
        self.stdout = f"{json.dumps(payload) if payload is not None else ''}\n{status}"
        self.stderr = ""
        self.returncode = 0


def _install(login="kimeisele", selection="all", perms=None, active=True, iid=7):
    return {
        "id": iid, "account": {"login": login}, "active": active,
        "repository_selection": selection,
        "permissions": perms or {"metadata": "read", "contents": "read",
                                 "pull_requests": "read", "checks": "write"},
    }


def _run_finalize(monkeypatch, installations, cfg=None):
    monkeypatch.setattr(auth, "create_app_jwt", lambda app_id, key: "fake-jwt")

    def fake_run(args):
        if "--url" in args:
            url = args[args.index("--url") + 1]
            if "/app/installations" in url:
                return _FakeCompleted(installations)
            if "/app" in url:
                return _FakeCompleted({"slug": "federation-hq-review-gate", "id": 1})
        return _FakeCompleted(None, 404)

    monkeypatch.setattr("federation_hq_gate.http._run_curl", fake_run)
    return auth.finalize_installation(
        cfg or {"app_id": "12345", "private_key_path": "/nonexistent/key.pem"}
    )


def test_exact_kimeisele_installation_discovery(monkeypatch):
    assert _run_finalize(monkeypatch, [_install()]) == "7"


def test_no_matching_installation(monkeypatch):
    with pytest.raises(auth.AuthError, match="no active installation"):
        _run_finalize(monkeypatch, [_install(login="someone-else")])


def test_multiple_matching_installations(monkeypatch):
    with pytest.raises(auth.AuthError, match="multiple installations"):
        _run_finalize(monkeypatch, [_install(iid=1), _install(iid=2)])


def test_selected_repositories_installation_rejected(monkeypatch):
    with pytest.raises(auth.AuthError, match="not 'All repositories'"):
        _run_finalize(monkeypatch, [_install(selection="selected")])


def test_forbidden_permissions_rejected(monkeypatch):
    with pytest.raises(auth.AuthError, match="forbidden permission"):
        _run_finalize(monkeypatch, [_install(perms={
            "metadata": "read", "contents": "read",
            "pull_requests": "read", "checks": "write", "administration": "write",
        })])


def test_unexpected_extra_permission_rejected(monkeypatch):
    with pytest.raises(auth.AuthError, match="unexpected permission"):
        _run_finalize(monkeypatch, [_install(perms={
            "metadata": "read", "contents": "read",
            "pull_requests": "read", "checks": "write", "deployments": "read",
        })])


def test_secret_redaction_in_errors():
    text = "token ghp_abc123 and gho_def456 leaked"
    redacted = config.redact(text)
    assert "ghp_abc123" not in redacted
    assert "gho_def456" not in redacted
    assert "<redacted>" in redacted
