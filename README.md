# Federation HQ

<!-- BEGIN FEDERATION NODE IDENTITY -->
> **Node:** Federation HQ
> **Repository:** kimeisele/federation-hq
> **Tier:** Service Node
> **Role:** Capability provider — offer tools, APIs, or agent services to the federation.
>  
> ℹ️ The content above is managed by `scripts/setup_node.py`.
> The rest of this README is the generic federation-node handbook.
<!-- END FEDERATION NODE IDENTITY -->

**Federation service node and durable prompt-as-infrastructure repository for
bounded cross-repository engineering work.**

Federation HQ is a node of the [Agent Internet](https://github.com/kimeisele/agent-internet)
federation. It inherits the federation-node kernel from
[`kimeisele/agent-template`](https://github.com/kimeisele/agent-template):
authority publishing, peer discovery, agent card, and automated workflows.

## What Federation HQ is

- a **prompt and work-contract registry** — versioned role prompts
  (`prompts/`), machine-readable handoff contracts (`contracts/`), and example
  artifacts (`examples/`);
- a **cross-repository maintenance coordination workspace** — a durable record
  of scoped, bounded repair work (`runs/`);
- a **durable record of scoped agent work** — run manifests pinning target
  repository, baseline SHA, and exact prompt versions;
- a **producer of versioned repair workflow artifacts** — `repair_candidate`,
  `repair_result`, `independent_review_result`, `prompt_release`, `run_manifest`.

## What Federation HQ is not

- the federation runtime,
- the Agent Internet control plane,
- the Agent World authority,
- an Agent City replacement,
- a repository governor,
- a source of truth for target repository code,
- an autonomous merge authority,
- a CI bypass mechanism,
- a general-purpose self-directed coding agent.

Target repositories retain authority over their own architecture, governance,
code, CI, issues, pull requests, and merge decisions. See
[`docs/BOUNDARIES.md`](docs/BOUNDARIES.md) for the full boundary contract.

## The repair workflow (three execution roles, one coordinator)

The repair workflow separates three execution and judgment roles with three
independently versioned prompts, coordinated by a fourth role, the **HQ
Operator**:

| Role | Prompt | Does |
|------|--------|------|
| HQ Operator | `operator@0.1.0` | Coordinates the run via a GitHub Issue; exactly one active assignment at a time; no merge authority, no semantic repair judgment |
| Unwired Functionality Scout | `scout@0.1.0` | Investigates a bounded maintenance request and selects **exactly one** candidate |
| Targeted Repair Builder | `repair@0.1.0` | Repairs **exactly that** candidate, compares baseline-versus-head failures |
| Independent Repair Reviewer | `review@0.1.0` | Independently checks the **exact remote head** and records a verdict |

The Operator coordinates via structured Issue comments (see
[`docs/COORDINATION_PROTOCOL.md`](docs/COORDINATION_PROTOCOL.md)); committed
artifacts under `runs/<run-id>/` remain canonical. State transitions,
invariants (new commits invalidate prior approval; no role merges its own
work; no admin bypass; red CI compared baseline-versus-head), and artifacts
are documented in [`docs/REPAIR_PIPELINE.md`](docs/REPAIR_PIPELINE.md). Why
three separate prompts instead of one master prompt with a mode switch:
[`docs/decisions/ADR-0001-three-role-repair-workflow.md`](docs/decisions/ADR-0001-three-role-repair-workflow.md).

## Prompt versions and run artifacts

- Released prompt versions are **immutable**; every release records a changelog
  rationale in [`prompts/registry.yaml`](prompts/registry.yaml). See
  [`docs/PROMPT_VERSIONING.md`](docs/PROMPT_VERSIONING.md).
- Each registry version pins the **SHA-256 of its exact file bytes**, and each
  run manifest pins the exact `id`, `version`, and `sha256` of the operator,
  scout, repair, and review prompts — a run binds exact prompt content, not
  just a version label.
- Each run manifest binds the original bounded **`maintenance_request`**
  (text, source, created_at, optional reference); the Scout candidate may
  clarify it but may not replace or silently broaden it.
- Artifacts are validated against JSON Schemas in `contracts/` by
  `scripts/validate_artifacts.py` — structural validation only; it never proves
  semantic truth.

## Evidence: SHAs and PR heads

Evidence is anchored to verifiable references, never to prose:

- `baseline_sha` — the target repository commit the run started from;
- `repair_head_sha` / `reviewer_head_sha` — the exact commits produced and
  re-checked;
- branch and `pull_request` references — where the change lives in the target
  repository;
- `commands` with exit codes and outcomes — how claims were observed;
- `baseline_failures` vs `newly_introduced_failures` — red CI is compared
  baseline-versus-head, never glossed over.

Reports and agent summaries are **claims to verify, not proof**.

## Optional: Engineering Encyclopedia

[`kimeisele/engineering-encyclopedia`](https://github.com/kimeisele/engineering-encyclopedia)
may be used optionally as a context source:

1. The Scout identifies and precisely describes one candidate.
2. A context pack may be generated from that concrete defect description.
3. The exact pack and its SHA-256 are pinned in the run manifest.
4. The Repair Builder receives the frozen pack.
5. The Reviewer independently validates all semantic claims against code and
   evidence.

Federation HQ does not vendor the corpus, does not make the encyclopedia
responsible for discovering arbitrary bugs, and does not treat its report
verifier as proof that code is correct. See
[`docs/BOUNDARIES.md`](docs/BOUNDARIES.md).

## Current manual workflow (v0.1.0)

In v0.1.0 the workflow is **manually advanced and coordinated by the HQ
Operator** — there is no dispatcher, no automatic model invocation, and no
autonomous PR creation or merging. Coordination happens through structured
comments on the run's GitHub Issue (see
[`docs/COORDINATION_PROTOCOL.md`](docs/COORDINATION_PROTOCOL.md));
"dispatch" means posting a structured role assignment into that Issue, which
an external human or agent reads and executes:

1. A bounded `repository_maintenance_request` arrives; the Operator creates a
   run directory under `runs/` with a `run-manifest` pinning target, baseline
   SHA, the original `maintenance_request`, the `coordination` Issue
   reference, and the exact operator/scout/repair/review prompt versions with
   content hashes, then opens the run's coordination Issue.
2. The Operator posts one assignment at a time; the Scout records exactly one
   `repair-candidate`, which the Operator structurally validates and accepts
   or rejects as a whole.
3. The Operator routes the accepted candidate to the Repair Builder, who
   records a `repair-result` (head SHA, PR reference, commands,
   baseline/newly-introduced failures).
4. The Operator routes the accepted result to the Reviewer, who checks the
   exact remote head and records a `review-result` verdict.
5. The Operator updates the run's `pipeline_state` at each permitted
   transition and closes the Issue at a terminal state. Artifacts are
   validated with `scripts/validate_artifacts.py`.

## Explicitly deferred

Not implemented in this bootstrap, documented as deferred possibilities (not
promised roadmap commitments):

- automatic model invocation and agent dispatch,
- a microkernel or generalized federation protocol,
- job queues and scheduled repository scanning,
- automatic repository selection,
- autonomous PR creation and merging in target repositories,
- model routing,
- dashboards, databases, and web servers,
- any replacement for repository-native governance.

## Federation Bootstrap Program (planning)

The planning-only program context is identified as
`federation-bootstrap-v0.1` and is kept in two separate artifacts:

- [`docs/FEDERATION_BOOTSTRAP_PROGRAM.md`](docs/FEDERATION_BOOTSTRAP_PROGRAM.md)
  is the architecture and program brief (Artifact A).
- [`docs/FEDERATION_BOOTSTRAP_DIRECTOR_INSTRUCTION.md`](docs/FEDERATION_BOOTSTRAP_DIRECTOR_INSTRUCTION.md)
  is the bootstrap instruction for the Mission Director (Artifact B).

Artifact B references Artifact A as context. Neither artifact is a mission,
run, policy, or authorization to modify a target repository. Program progress
becomes durable only through the existing mission ledger and canonical run
artifacts after the Director instruction is initiated.

## Development

```bash
pip install -e ".[dev]"

# Inherited kernel checks
python -m pytest tests/ -q
python -m ruff check .

# Artifact contract validation
python scripts/validate_artifacts.py

# Regenerate descriptors / agent card (do not edit .well-known by hand)
python scripts/render_federation_descriptor.py
python scripts/render_agent_card.py
```

The default branch is protected by the `agent-federation-baseline-v1` ruleset
(no deletion, no force push, pull requests required). Local changes go through
a pull request. See
[`docs/governance/DEFAULT_BRANCH_GOVERNANCE_VALIDATION.md`](docs/governance/DEFAULT_BRANCH_GOVERNANCE_VALIDATION.md).

**Sync posture:** the `sync-agent-card` and `sync-federation-descriptor`
workflows do not push generated content to `main`. Generated identity surfaces
(`.well-known/`) are regenerated and committed through normal feature branches
and PRs; CI renders them and **fails on drift**. After merging your setup PR,
CI checks the committed surfaces against the renderers on every relevant push
and pull request. The authority-feed workflow publishes only to the separate
`authority-feed` publication branch. The weekly peer-discovery workflow is a
**read-only scheduled health check**: it discovers federation peers and
verifies their authority feeds without committing or pushing to any branch.

## Repository map

| Path | Purpose |
|------|---------|
| `prompts/` | Versioned role prompts (operator, scout, repair, review) and `registry.yaml` |
| `contracts/` | JSON Schemas for run/candidate/result/review artifacts and coordination messages |
| `examples/` | One valid YAML example per schema |
| `runs/` | Durable run records (one directory per run) |
| `docs/` | Boundaries, pipeline, coordination protocol, versioning, planning artifacts, ADRs |
| `docs/COORDINATION_PROTOCOL.md` | GitHub-native coordination protocol v0.1 (message envelope + comment examples) |
| `.github/ISSUE_TEMPLATE/` | HQ Run / HQ Change / HQ Defect plain-Markdown Issue templates |
| `scripts/validate_artifacts.py` | Structural artifact validation |
| `docs/authority/` | Charter and capability manifest (generated by setup, then customized) |
| `.well-known/` | Federation descriptor and agent card (auto-generated) |
