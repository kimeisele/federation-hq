# AGENTS.md

Instructions for AI coding agents working in Federation HQ. Read this file
completely before doing anything else in this repository.

## Repository purpose

Federation HQ is the durable registry and coordination workspace for:

- versioned role prompts (`prompts/`),
- bounded repository-maintenance requests (pinned in run manifests),
- run manifests (`runs/`),
- Scout candidates,
- Repair results,
- independent Review results,
- verifiable repository/SHA/PR evidence.

It is a federation service node, not a runtime: it does not dispatch agents,
merge into target repositories, or replace their governance.

## Authority hierarchy

When sources conflict, use this order:

1. `AGENTS.md` — repository collaboration and operating rules.
2. `docs/BOUNDARIES.md` — architectural ownership boundaries.
3. `docs/REPAIR_PIPELINE.md` — the current three-role workflow.
4. Released prompts and their registry entries (`prompts/registry.yaml`) —
   role behavior.
5. JSON Schemas (`contracts/`) and validator behavior
   (`scripts/validate_artifacts.py`) — structural contracts.
6. Run artifacts under `runs/` — one particular execution.
7. Agent reports and summaries — claims requiring verification, never proof.

## Binding rules

- **Target repositories remain authoritative** for their own code, governance,
  CI, issues, PRs, and merge decisions. Federation HQ records evidence; it does
  not govern them.
- **Released prompt versions are immutable.** Corrections require a new
  version (or an explicitly documented unreleased-bootstrap amendment before
  the repository's first merge — see `docs/PROMPT_VERSIONING.md`). Never edit
  a released prompt file and keep its release identity.
- **Do not silently expand the three-role workflow.** Scout selects exactly one
  candidate; Repair repairs only that candidate; Review checks the exact remote
  head. New roles or phases are a documented decision, not an inline addition.
- **Amend, don't proliferate.** Do not create new governance, north-star,
  handoff, or architecture documents when an existing canonical document
  (`docs/BOUNDARIES.md`, `docs/REPAIR_PIPELINE.md`, `docs/PROMPT_VERSIONING.md`,
  ADRs) can be amended.
- **Deferred is not authorized.** Do not begin implementation merely because an
  idea appears in the explicitly-deferred lists in `README.md`.
- **No admin bypass, self-review, or self-merge.** Not in Federation HQ, not in
  target repositories.
- **Do not modify inherited federation-kernel code** (setup wizard, renderers,
  Nadi transport, governance, authority feed, discovery) unless a reproduced
  defect or a necessary HQ compatibility issue requires it — and then with a
  recorded reason.
- **Externalize decisions.** Every lasting workflow decision must be recorded
  in an existing appropriate document, an ADR, an Issue, or a PR record.

## Build, test and validation

```bash
pip install -e ".[dev]"            # dev tooling (pytest, ruff, pyyaml)
pip install -e ".[federation]"     # optional nadi-kit (needed for nadi tests)

python -m pytest tests/ -q         # full suite
python -m pytest tests/test_artifact_contracts.py -q   # artifact contracts
python -m pytest tests/test_identity_isolation.py -q   # identity pollution guards
python -m ruff check .

python scripts/validate_artifacts.py          # registry + examples + runs/
python scripts/validate_artifacts.py --artifact runs/<run>/repair-result.yaml

# Regenerate identity surfaces (do NOT edit .well-known by hand)
python scripts/render_federation_descriptor.py --repo kimeisele/federation-hq --layer node
python scripts/render_agent_card.py --repo kimeisele/federation-hq

python scripts/quickstart.py       # node health check (read-only renders)
```

## Generated files

Do not edit generated files directly:

- `.well-known/agent-federation.json` and `.well-known/agent.json` — rendered
  by `scripts/render_*.py`; CI fails on drift.
- `docs/authority/charter.md`, `docs/authority/capabilities.json`,
  `data/federation/peer.json`, README identity block — written by
  `scripts/setup_node.py`; `capabilities.json` may then be customized as the
  authoritative manifest (the setup wizard regenerates it from tier defaults,
  so customize after running setup).
- `.federation-setup.json` and `.federation/` — local, gitignored. Tests must
  never write them; they are regenerable.

## Tests must not touch the real checkout

Identity-generation tests use isolated temporary roots. Never write
`.federation-setup.json`, `.well-known/`, or any committed identity file from
a test. Regression coverage lives in `tests/test_identity_isolation.py`.

## Git workflow

- Feature branches, PRs to `main`. `main` is protected by the
  `agent-federation-baseline-v1` ruleset (deletion, non-fast-forward,
  pull-request required; no bypass actors).
- Sync workflows do not push to `main`; generated identity surfaces are
  regenerated and committed through normal PRs, and CI checks them for drift.
- Federation discovery is a read-only scheduled health check: it discovers
  peers and verifies authority feeds without committing or pushing to any
  branch.
- Bot identity `federation-bot` / `bot@federation` is used only by workflows
  that publish to non-default publication branches (e.g. authority feed).
