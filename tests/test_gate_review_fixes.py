"""Regression tests for the review-fix blockers (A/B/C) on the policy apply
safety path: verification fail-closed with restore, durable atomic backup
before mutation, and round-trippable normalized snapshots. Mocks only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from federation_hq_gate import policy  # noqa: E402
from tests.test_gate_policy_safety import _classic, _gh_repo  # noqa: E402

GATE_RULESET = {
    "id": 900, "name": policy.GATE_RULESET_NAME,
    "rules": [{"type": "required_status_checks", "parameters": {"checks": [
        {"context": "federation-hq/review", "integration_id": 4528340}]}}],
}


class OrderRecorder:
    """Records operation order and supports a verification-failure mode."""

    def __init__(self, mirror_puts: bool = True, verify_ok: bool = True,
                 reflect_deletes: bool = True) -> None:
        self.gets: dict[str, object] = {}
        self.puts: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self.events: list[str] = []
        self.mirror_puts = mirror_puts
        self.verify_ok = verify_ok
        self.reflect_deletes = reflect_deletes

    def gh_get(self, path: str):
        if path in self.gets:
            return self.gets[path]
        raise AssertionError(f"unexpected gh GET {path}")

    def gh_put(self, path: str, body: dict):
        self.puts.append((path, body))
        self.events.append(f"put:{path}")
        if self.mirror_puts and (path.endswith("/protection") or "/rulesets/" in path):
            self.gets[path] = body
        elif self.mirror_puts:
            self.gets[path] = body
        return body

    def gh_delete(self, path: str):
        self.deletes.append(path)
        self.events.append(f"delete:{path}")
        if not self.reflect_deletes:
            return
        if "/rulesets/" in path and self.gets.get(
            "/repos/kimeisele/federation-hq/rulesets"
        ) is not None:
            self.gets["/repos/kimeisele/federation-hq/rulesets"] = []
        elif path.endswith("/protection"):
            self.gets[path] = None
        else:
            self.gets.pop(path, None)


def _plan(rec, repos, includes=None):
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = repos
    for repo in repos:
        rec.gets[f"/repos/{repo['full_name']}/branches/{repo['default_branch']}/protection"] = _classic()
        rec.gets[f"/repos/{repo['full_name']}/rulesets"] = []
        rec.gets[f"/repos/{repo['full_name']}"] = repo
    monkey = pytest.MonkeyPatch()
    monkey.setattr(policy, "gh_get", rec.gh_get)
    try:
        return policy.build_plan("kimeisele", set(), includes)
    finally:
        monkey.undo()


def _apply(rec, plan, *, app_id="4528340", bootstrap_ok=True, tmp_path=None):
    monkey = pytest.MonkeyPatch()
    monkey.setattr(policy, "_bootstrap_repo",
                   lambda fn, full, branch: (bootstrap_ok, "bootstrapped" if bootstrap_ok else "boom"))
    monkey.setattr(policy, "gh_get", rec.gh_get)
    monkey.setattr(policy, "gh_put", rec.gh_put)
    monkey.setattr(policy, "gh_delete", rec.gh_delete)
    try:
        return policy.apply_plan(
            plan, expected_sha256=plan["plan_sha256"],
            app_installation_token_fn=lambda **k: "t", dry_run=False,
            app_id=app_id, backup_dir=tmp_path or Path("/tmp"),
        )
    finally:
        monkey.undo()


# ── Blocker A: verification fail-closed with before-state restore ──────────


def test_verification_failure_reported_as_failed_not_configured(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)  # remote never reflects the write
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    entry = report["repositories"][0]
    assert entry["status"] == "failed"
    assert "remote verification failed" in entry["reason"]


def test_verification_failure_restores_before_state(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    entry = report["repositories"][0]
    # The before-state classic protection must be restored via PUT.
    restore_puts = [p for p in rec.puts if p[0].endswith("/branches/main/protection")]
    assert restore_puts, "no restore PUT after verification failure"
    assert entry["rollback"]["status"] == "restored"


def test_rollback_failure_reported_explicitly(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])

    protection_put_count = 0

    def failing_put(path, body):
        nonlocal protection_put_count
        if path.endswith("/branches/main/protection"):
            protection_put_count += 1
            if protection_put_count == 1:
                # configure write: succeeds but is not reflected remotely
                return body
            raise policy.PolicyError("restore failed")
        return body

    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", failing_put)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    entry = report["repositories"][0]
    assert entry["status"] == "failed"
    assert entry["rollback"]["status"] == "failed"


# ── Blocker B: durable atomic backup before mutation ───────────────────────


def test_backup_written_before_any_mutation(tmp_path, monkeypatch):
    rec = OrderRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    original_write_atomic = policy._write_atomic

    def recording_write_atomic(path: Path, data: dict):
        rec.events.append("backup")
        original_write_atomic(path, data)

    monkeypatch.setattr(policy, "_write_atomic", recording_write_atomic)
    _apply(rec, plan, tmp_path=tmp_path)
    # The durable backup write must precede every protection mutation.
    assert rec.events[0] == "backup"
    assert rec.puts, "expected at least one protection write"
    assert all("put:" in e or "delete:" in e for e in rec.events[1:])


def test_backup_is_atomic_and_durable(tmp_path, monkeypatch):
    rec = OrderRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    backup_path = Path(report["backup_path"])
    assert backup_path.exists()
    assert not backup_path.with_suffix(backup_path.suffix + ".tmp").exists()
    backup = json.loads(backup_path.read_text())
    assert "kimeisele/federation-hq" in backup
    assert backup["kimeisele/federation-hq"]["protection"]["classic"] is not None


def test_backup_persists_after_write_for_manual_rollback(tmp_path, monkeypatch):
    rec = OrderRecorder()
    plan = _plan(rec, [_gh_repo("kimeisele/federation-hq")])
    report = _apply(rec, plan, tmp_path=tmp_path)
    assert report["repositories"][0]["status"] == "configured"
    backup_path = Path(report["backup_path"])
    assert backup_path.exists()
    data = json.loads(backup_path.read_text())
    assert "kimeisele/federation-hq" in data  # durable before-state for manual rollback


# ── Blocker C: round-trippable normalized snapshots ────────────────────────


def test_gate_ruleset_full_representation_fetched_before_mutation(monkeypatch, tmp_path):
    rec = OrderRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = dict(GATE_RULESET)
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    plan = policy.build_plan("kimeisele", set(), set())
    snapshot = plan["repositories"][0]["protection_snapshot"]
    gate = [r for r in snapshot["rulesets"] if r["name"] == policy.GATE_RULESET_NAME][0]
    assert "write_safe" in gate  # full representation stored, normalized
    assert "id" not in gate["write_safe"]
    assert gate["write_safe"]["rules"] == GATE_RULESET["rules"]


def test_ruleset_restore_payload_is_normalized_update_payload(monkeypatch, tmp_path):
    rec = OrderRecorder()
    before_gate = {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": [{"type": "deletion"}]}
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": [dict(before_gate)]}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = dict(GATE_RULESET)
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    policy.rollback(backup)
    path, body = rec.puts[0]
    assert path == "/repos/kimeisele/federation-hq/rulesets/900"
    assert body == policy._normalize_ruleset_for_write(before_gate)
    assert "id" not in body and "created_at" not in body


def test_rollback_verification_detects_non_matching_gate_ruleset(monkeypatch, tmp_path):
    rec = OrderRecorder(mirror_puts=False)
    before_gate = {"id": 900, "name": policy.GATE_RULESET_NAME, "rules": [{"type": "deletion"}]}
    before = {"default_branch": "main",
              "protection": {"classic": None, "rulesets": [dict(before_gate)]}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    # The full current ruleset DIFFERS from the stored representation.
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = {
        "id": 900, "name": policy.GATE_RULESET_NAME, "rules": [{"type": "non_fast_forward"}],
    }
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    verification = report["results"][0]["verification"]
    assert verification["ok"] is False
    assert any("not restored to its previous representation" in p for p in verification["problems"])


# ── Final review fixes: full ruleset fetch, enabled decoding, rollback status


def test_ruleset_verification_fetches_full_gate_ruleset(monkeypatch):
    """Verification uses the individual ruleset endpoint, not the summary."""
    rec = OrderRecorder(mirror_puts=False)
    # List response is a SUMMARY without a rules array.
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "enforcement": "active"}
    ]
    # The FULL representation (with the actual rules) lives on the
    # individual endpoint.
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = {
        "id": 900, "name": policy.GATE_RULESET_NAME, "rules": [
            {"type": "required_status_checks", "parameters": {"checks": [
                {"context": "federation-hq/review", "integration_id": 4528340}]}},
        ]}
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is True
    assert verify["required_check_app_bound"] is True


def test_ruleset_verification_rejects_wrong_integration_id(monkeypatch):
    rec = OrderRecorder(mirror_puts=False)
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [
        {"id": 900, "name": policy.GATE_RULESET_NAME, "enforcement": "active"}]
    rec.gets["/repos/kimeisele/federation-hq/rulesets/900"] = {
        "id": 900, "name": policy.GATE_RULESET_NAME, "rules": [
            {"type": "required_status_checks", "parameters": {"checks": [
                {"context": "federation-hq/review", "integration_id": 999}]}},
        ]}
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    verify = policy._verify_protection("kimeisele/federation-hq", "main", "4528340")
    assert verify["ok"] is False


def test_github_enabled_object_decoding():
    assert policy._github_enabled({"enabled": False}) is False
    assert policy._github_enabled({"enabled": True}) is True
    assert policy._github_enabled(True) is True
    assert policy._github_enabled(False) is False
    assert policy._github_enabled(None) is False
    assert policy._github_enabled({}) is False


def test_normalize_does_not_enable_force_pushes_from_false_object():
    raw = {
        "required_status_checks": {"strict": True, "contexts": ["ci"]},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }
    normalized = policy._normalize_classic_for_write(raw)
    assert normalized["allow_force_pushes"] is False
    assert normalized["allow_deletions"] is False
    assert normalized["enforce_admins"] is True


def test_configure_cannot_enable_force_pushes_from_false_get_state(monkeypatch, tmp_path):
    rec = OrderRecorder()
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = {
        "required_status_checks": {"strict": True, "contexts": ["ci"]},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    assert report["repositories"][0]["status"] == "configured"
    body = rec.puts[0][1]
    assert body["allow_force_pushes"] is False
    assert body["allow_deletions"] is False


def test_rollback_verified_success_returns_restored(tmp_path, monkeypatch):
    rec = OrderRecorder()
    before = {"default_branch": "main", "protection": {"classic": None, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    assert report["results"][0]["status"] == "restored"
    assert report["results"][0]["verification"]["ok"] is True


def test_rollback_verification_failure_returns_failed(tmp_path, monkeypatch):
    rec = OrderRecorder(reflect_deletes=False)
    before = {"default_branch": "main", "protection": {"classic": None, "rulesets": []}}
    backup = tmp_path / "b.json"
    backup.write_text(json.dumps({"kimeisele/federation-hq": before}))
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = [dict(GATE_RULESET)]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = None
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    report = policy.rollback(backup)
    assert report["results"][0]["status"] == "failed"
    assert report["results"][0]["verification"]["ok"] is False


def test_apply_report_distinguishes_policy_and_rollback_outcomes(tmp_path, monkeypatch):
    rec = OrderRecorder(mirror_puts=False)  # remote never reflects the write
    rec.gets["/user/repos?affiliation=owner&per_page=100&page=1"] = [
        _gh_repo("kimeisele/federation-hq")]
    rec.gets["/repos/kimeisele/federation-hq/branches/main/protection"] = _classic()
    rec.gets["/repos/kimeisele/federation-hq/rulesets"] = []
    rec.gets["/repos/kimeisele/federation-hq"] = _gh_repo("kimeisele/federation-hq")
    monkeypatch.setattr(policy, "gh_get", rec.gh_get)
    monkeypatch.setattr(policy, "gh_put", rec.gh_put)
    monkeypatch.setattr(policy, "gh_delete", rec.gh_delete)
    monkeypatch.setattr(policy, "_bootstrap_repo",
                        lambda fn, full, branch: (True, "bootstrapped"))
    plan = policy.build_plan("kimeisele", set(), set())
    report = policy.apply_plan(
        plan, expected_sha256=plan["plan_sha256"],
        app_installation_token_fn=lambda **k: "t", dry_run=False,
        app_id="4528340", backup_dir=tmp_path,
    )
    entry = report["repositories"][0]
    assert entry["status"] == "failed"
    assert entry["policy_verification"]["ok"] is False
    assert entry["rollback"]["status"] == "restored"
