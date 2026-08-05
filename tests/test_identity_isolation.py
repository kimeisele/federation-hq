"""Regression tests for the identity-pollution path.

History: tests that exercised the real ``setup_node.apply_config`` wrote the
gitignored ``.federation-setup.json`` into the real checkout with a test
repository identity (``test-org/test-repo``). A later renderer/quickstart run
read ``--repo`` from that file and overwrote the committed
``.well-known/agent-federation.json`` with the test identity.

These tests prove:
- applying a test identity cannot mutate committed/generated identity files;
- no test identity survives in any committed identity surface;
- all authoritative and generated surfaces agree on the real identity.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

TEST_CONFIG = {
    "display_name": "Test",
    "repo_name": "test-repo",
    "github_repo": "test-org/test-repo",
    "description": "Test",
    "tier": "relay",
    "domains": [],
    "custom_skills": [],
    "values": "",
    "role_id": "test_repo_relay",
    "city_zone": "general",
}

COMMITTED_IDENTITY_FILES = [
    ".well-known/agent-federation.json",
    ".well-known/agent.json",
    "docs/authority/capabilities.json",
    "docs/authority/charter.md",
    "data/federation/peer.json",
    "README.md",
]

TEST_IDENTITY_MARKERS = ("test-org", "test-repo", "test_repo_surface", "test_repo_relay")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot() -> dict[str, str]:
    snap = {}
    for name in COMMITTED_IDENTITY_FILES:
        path = REPO_ROOT / name
        snap[name] = _sha256(path) if path.exists() else "<missing>"
    cfg = REPO_ROOT / ".federation-setup.json"
    snap[".federation-setup.json"] = _sha256(cfg) if cfg.exists() else "<missing>"
    return snap


def _run_apply_config_with_test_identity(monkeypatch, tmp_path: Path) -> object:
    """Invoke the real apply_config with the test identity, fully isolated."""
    from setup_node import (
        ComplianceStatus,
        IdentitySource,
        SetupContext,
        TopicRegistration,
        TopicResult,
        apply_config,
    )

    reg = TopicRegistration(
        result=TopicResult.SKIPPED_OFFLINE,
        repository="test-org/test-repo",
        topics_before=[],
        topics_after=[],
        message="mock",
        remote_attempted=False,
    )
    ctx = SetupContext(identity_source=IdentitySource.EXPLICIT, allow_remote_writes=False)

    monkeypatch.setattr(sys.modules["setup_node"], "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        sys.modules["setup_node"], "_register_federation_topic", lambda *a, **kw: reg
    )
    monkeypatch.setattr(
        sys.modules["setup_node"], "_run_governance_step",
        lambda **kw: ComplianceStatus.UNKNOWN,
    )
    for fn_name in (
        "_write_charter", "_write_capabilities", "_write_readme_identity",
        "_regenerate", "_write_peer_json", "_print_topic_result",
        "_print_readme_result",
    ):
        monkeypatch.setattr(sys.modules["setup_node"], fn_name, lambda *a, **kw: None)

    return apply_config(TEST_CONFIG, ctx=ctx, interactive=False, apply_governance=False)


def test_apply_config_cannot_mutate_committed_identity_files(monkeypatch, tmp_path: Path) -> None:
    """Applying a test identity must leave every committed identity file untouched."""
    before = _snapshot()
    _run_apply_config_with_test_identity(monkeypatch, tmp_path)
    after = _snapshot()
    assert before == after, (
        "apply_config with a test identity mutated committed identity files; "
        "the pollution path is back"
    )


def test_apply_config_writes_only_into_isolated_root(monkeypatch, tmp_path: Path) -> None:
    """The test identity must land in the isolated root, not the real checkout."""
    _run_apply_config_with_test_identity(monkeypatch, tmp_path)
    assert (tmp_path / ".federation-setup.json").exists()
    saved = json.loads((tmp_path / ".federation-setup.json").read_text())
    assert saved["github_repo"] == "test-org/test-repo"
    assert not (REPO_ROOT / ".federation-setup.json").exists() or (
        json.loads((REPO_ROOT / ".federation-setup.json").read_text()).get("github_repo")
        != "test-org/test-repo"
    )


@pytest.mark.parametrize("name", COMMITTED_IDENTITY_FILES)
def test_no_test_identity_in_committed_surfaces(name: str) -> None:
    """None of the committed identity surfaces may carry test identity values."""
    path = REPO_ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present in this checkout")
    text = path.read_text(encoding="utf-8")
    for marker in TEST_IDENTITY_MARKERS:
        assert marker not in text, f"{name} contains test identity marker {marker!r}"


def test_identity_surfaces_agree_on_federation_hq() -> None:
    """All authoritative and generated surfaces identify federation-hq."""
    descriptor = json.loads((REPO_ROOT / ".well-known" / "agent-federation.json").read_text())
    card = json.loads((REPO_ROOT / ".well-known" / "agent.json").read_text())
    caps = json.loads((REPO_ROOT / "docs" / "authority" / "capabilities.json").read_text())
    charter = (REPO_ROOT / "docs" / "authority" / "charter.md").read_text()
    readme = (REPO_ROOT / "README.md").read_text()

    assert descriptor["repo_id"] == "federation-hq"
    assert descriptor["display_name"] == "Federation HQ"
    assert descriptor["owner_boundary"] == "federation_hq_surface"
    assert descriptor["authority_feed_manifest_url"].startswith(
        "https://raw.githubusercontent.com/kimeisele/federation-hq/"
    )
    assert caps["node_id"] == "federation-hq"
    assert caps["display_name"] == "Federation HQ"
    assert card["name"] == "Federation HQ"
    assert card["url"] == "https://github.com/kimeisele/federation-hq"
    assert "Federation HQ" in charter
    assert "Federation HQ" in readme
    assert "kimeisele/federation-hq" in readme


def test_local_setup_config_has_no_test_identity() -> None:
    """The local (gitignored) setup config must not carry test identity either."""
    cfg = REPO_ROOT / ".federation-setup.json"
    if not cfg.exists():
        pytest.skip("no local setup config present")
    data = json.loads(cfg.read_text())
    assert data.get("github_repo") != "test-org/test-repo"
