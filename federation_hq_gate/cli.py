"""federation-hq-gate command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_cfg_or_exit() -> dict:
    from .config import GateConfigError, load_config
    try:
        return load_config()
    except GateConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


def cmd_setup_app(args: argparse.Namespace) -> int:
    from . import app_setup
    if args.manual:
        app_setup.run_manual_flow()
        return 0
    if args.manual_store:
        app_setup.store_manual_credentials(
            args.app_id, args.installation_id, args.pem_path
        )
        print("Run: python -m federation_hq_gate doctor")
        return 0
    if args.finalize_install:
        from .auth import AuthError, finalize_installation
        from .config import GateConfigError, load_config, persist_installation_id
        try:
            cfg = load_config(require_installation=False)
            discovered = finalize_installation(cfg)
            persist_installation_id(discovered, expected_app_id=cfg["app_id"])
        except (GateConfigError, AuthError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"installation finalized: installation_id {discovered} persisted (mode 0600)")
        print("Run: python -m federation_hq_gate doctor")
        return 0
    try:
        credentials = app_setup.run_manifest_flow()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    from .config import store_credentials
    key_path = store_credentials(
        str(credentials["id"]), credentials["pem"],
        credentials.get("webhook_secret"), credentials.get("slug", app_setup.MANIFEST_NAME),
    )
    app_setup.cleanup_temporary_manifest()
    print(f"App credentials stored outside the repository at {key_path.parent}")
    print("Private key permissions set to owner-read/write only (0600).")
    print("Next: install the app on personal account kimeisele with 'All repositories':")
    print("  https://github.com/apps/federation-hq-review-gate/installations/new")
    print("Then finalize and verify:")
    print("  python -m federation_hq_gate setup-app --finalize-install")
    print("  python -m federation_hq_gate doctor")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import run_doctor
    report = run_doctor()
    for check in report.checks:
        marker = "ok" if check["ok"] else "FAIL"
        print(f"  [{marker}] {check['name']}: {check['detail']}")
    if not report.ok:
        print("doctor: UNSAFE configuration", file=sys.stderr)
        return 1
    print("doctor: configuration safe")
    return 0


def cmd_publish_review_check(args: argparse.Namespace) -> int:
    from .auth import AuthError, installation_token
    from .config import GateConfigError
    from .http import GitHubError
    from . import review_check

    review_path = Path(args.review_result)
    try:
        artifacts = review_check.resolve_run_artifacts(args.run_id, review_path)
        summary = review_check.validate_review_chain(
            artifacts, run_id=args.run_id, repository=args.repository, head_sha=args.head_sha,
            expected_repair_hash=args.expected_repair_result_sha256,
            expected_review_hash=args.expected_review_result_sha256,
        )
        cfg = _load_cfg_or_exit()
        token = installation_token(cfg, owner=args.repository.split("/", 1)[0],
                                   repo=args.repository.split("/", 1)[1])
        pr_number = review_check.verify_remote_pr_head(token, args.repository, args.head_sha)
        result = review_check.publish_success_check(
            token, args.repository, args.head_sha, summary, pr_number, cfg["app_id"]
        )
    except (review_check.ReviewGateError, AuthError, GateConfigError, GitHubError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.get("idempotent"):
        print("check already published for this run/repository/head (idempotent no-op)")
    else:
        run = result.get("check_run") or {}
        print(f"published federation-hq/review -> {run.get('html_url') or run.get('id')}")
    print(f"run_id: {summary['run_id']}")
    print(f"repository: {summary['repository']}")
    print(f"pr_number: {pr_number if pr_number is not None else 'n/a'}")
    print(f"baseline_sha: {summary['baseline_sha']}")
    print(f"reviewed_head_sha: {summary['reviewed_head_sha']}")
    print(f"repair_result_sha256: {summary['repair_result_sha256']}")
    print(f"review_result_sha256: {summary['review_result_sha256']}")
    print(f"verdict: {summary['verdict']}")
    print(f"blocker_count: {summary['blocker_count']}")
    print(f"canonical_record: {summary['canonical_record']}")
    return 0


def cmd_bootstrap_check(args: argparse.Namespace) -> int:
    from .auth import AuthError, installation_token
    from .config import GateConfigError
    from .http import GitHubError
    from . import bootstrap
    try:
        cfg = _load_cfg_or_exit()
        token = installation_token(cfg, owner=args.repository.split("/", 1)[0],
                                   repo=args.repository.split("/", 1)[1])
        branch = bootstrap.default_branch(token, args.repository)
        head = bootstrap.branch_head_sha(token, args.repository, branch)
        data = bootstrap.publish_bootstrap_check(token, args.repository, head)
    except (AuthError, GateConfigError, GitHubError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"bootstrap check published on default branch {branch} at {head}")
    print("This is registration/bootstrap evidence, not an approved repair review.")
    print(f"check_run: {data.get('html_url') or data.get('id')}")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    from . import policy
    if args.action == "plan":
        exclusions = set(args.exclude or [])
        plan = policy.build_plan(args.owner, exclusions)
        output = Path(args.output)
        output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"policy plan written to {output}")
        print(f"plan_sha256: {plan['plan_sha256']}")
        configured = [e for e in plan["repositories"] if not e.get("skip_reason")]
        print(f"planned repositories: {len(configured)} (skipped: {len(plan['skipped'])})")
        print("dry-run: no mutations performed")
        return 0
    if args.action == "apply":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        expected = args.confirm_plan_sha256
        dry_run = not args.confirm_plan_sha256 or args.dry_run
        try:
            from .auth import installation_token
            from .config import load_config
            cfg = load_config()
            token_fn = lambda owner=None, repo=None: installation_token(cfg, owner=owner, repo=repo)  # noqa: E731
            report = policy.apply_plan(
                plan, expected_sha256=expected, app_installation_token_fn=token_fn,
                dry_run=dry_run, app_id=cfg.get("app_id"),
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as error
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.action == "rollback":
        from . import policy
        report = policy.rollback(Path(args.backup))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="federation_hq_gate")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup-app", help="one-time GitHub App creation/installation")
    p_setup.add_argument("--manual", action="store_true",
                         help="print exact fields the owner must enter (fallback)")
    p_setup.add_argument("--manual-store", action="store_true",
                         help="store manually provided credentials")
    p_setup.add_argument("--finalize-install", action="store_true",
                         help="discover and persist the installation ID after the owner installs the App")
    p_setup.add_argument("--app-id")
    p_setup.add_argument("--installation-id")
    p_setup.add_argument("--pem-path")
    p_setup.set_defaults(func=cmd_setup_app)

    p_doctor = sub.add_parser("doctor", help="verify safe configuration")
    p_doctor.set_defaults(func=cmd_doctor)

    p_pub = sub.add_parser("publish-review-check",
                           help="publish federation-hq/review on the exact reviewed head")
    p_pub.add_argument("--repository", required=True)
    p_pub.add_argument("--head-sha", required=True)
    p_pub.add_argument("--run-id", required=True)
    p_pub.add_argument("--review-result", required=True)
    p_pub.add_argument("--expected-repair-result-sha256", default=None,
                       help="accepted repair-result hash recorded at acceptance (optional pin)")
    p_pub.add_argument("--expected-review-result-sha256", default=None,
                       help="accepted review-result hash recorded at acceptance (optional pin)")
    p_pub.set_defaults(func=cmd_publish_review_check)

    p_boot = sub.add_parser("bootstrap-check",
                            help="publish registration bootstrap check on the default branch")
    p_boot.add_argument("--repository", required=True)
    p_boot.set_defaults(func=cmd_bootstrap_check)

    p_policy = sub.add_parser("policy", help="two-phase branch-policy planning/application")
    p_policy.add_argument("action", choices=["plan", "apply", "rollback"])
    p_policy.add_argument("--owner", default="kimeisele")
    p_policy.add_argument("--output", default="policy-plan.json")
    p_policy.add_argument("--exclude", action="append", default=[],
                          help="OWNER/REPO to exclude (repeatable)")
    p_policy.add_argument("--plan", default="policy-plan.json")
    p_policy.add_argument("--confirm-plan-sha256", default="")
    p_policy.add_argument("--dry-run", action="store_true")
    p_policy.add_argument("--backup")
    p_policy.set_defaults(func=cmd_policy)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
