# Federation HQ Boundaries

This document defines the explicit non-overlap between Federation HQ and
every system it may interact with. It is the authority for *what this node
is not*. Where this document and any other file disagree, this document
wins for questions of scope.

## What Federation HQ is

- A **prompt and work-contract registry**: versioned role prompts, machine-readable
  handoff contracts, example artifacts, and a changelog for each.
- A **cross-repository maintenance coordination workspace**: a durable record of
  scoped, bounded repair work performed against target repositories.
- A **durable record of scoped agent work**: run manifests, repair candidates,
  repair results, and independent review results are pinned and retained.
- A **producer of versioned repair workflow artifacts**: `repair_candidate`,
  `repair_result`, `independent_review_result`, `prompt_release`, `run_manifest`.

## What Federation HQ is not

Federation HQ is **not**:

- the federation runtime,
- the Agent Internet control plane,
- the Agent World authority,
- an Agent City replacement,
- a repository governor,
- a source of truth for target repository code,
- an autonomous merge authority,
- a CI bypass mechanism,
- a general-purpose self-directed coding agent.

Nothing in this repository changes the authority of a target repository over
its own architecture, governance, code, CI, issues, pull requests, or merge
decisions.

## Explicit non-overlap

### `agent-world`

- **Theirs:** registry of agents, policies, heartbeat aggregation, world-level truth.
- **Ours:** a bounded record of repair runs and the prompts/contracts that drive them.
- **No overlap:** Federation HQ publishes no world registry, no global policy, and
  claims no authority over other nodes.

### `agent-city`

- **Theirs:** local runtime — Rathaus, Marktplatz, Pokedex census, zone governance.
- **Ours:** a single service node in the Engineering zone; a workspace for repair work.
- **No overlap:** Federation HQ does not replace city registration, census, or
  zone governance. Registration with Agent City remains the responsibility of
  the node operator through the documented issue template.

### `agent-internet`

- **Theirs:** control plane — Nadi relay, Lotus addressing, public membrane,
  discovery and routing, trust ledger.
- **Ours:** a node that publishes its own authority feed and is discoverable,
  exactly like any other federation node.
- **No overlap:** Federation HQ does not route messages, assign addresses, or
  project the public membrane.

### `steward` / `steward-protocol`

- **Theirs:** autonomous agent engine / identity-kernel substrate.
- **Ours:** a registry of prompts and contracts; work is executed by separate
  agents (in v0.1.0, manually).
- **No overlap:** Federation HQ does not run an agent runtime, does not hold
  kernel identity, and does not dispatch agents.

### `federation-recon`

- **Theirs:** reconnaissance and drift detection across the federation.
- **Ours:** scoped repair workflow artifacts for specific, bounded defects.
- **No overlap:** Federation HQ does not scan the federation, does not scan
  repositories for arbitrary bugs, and does not select targets automatically.

### `engineering-encyclopedia`

- **Theirs:** an optional corpus of engineering knowledge and a report verifier.
- **Ours:** optionally pins an exact context pack (by name and SHA-256) in a run
  manifest and feeds it to the Repair Builder.
- **No overlap:** Federation HQ does not vendor the corpus, does not make the
  encyclopedia responsible for discovering bugs, and does not treat its
  verifier as proof that code is correct.

### Target repositories

- **Theirs:** architecture, governance, code, CI, issues, pull requests, merge decisions.
- **Ours:** evidence references into the target repository — SHAs, PR heads,
  command outcomes — recorded in run artifacts.
- **No overlap:** Federation HQ never commits to, opens PRs in, or merges into a
  target repository in v0.1.0. All target-repository changes are performed and
  merged by the target repository's own maintainers through its own processes.

## Claims are not proof

Reports, agent summaries, and model-generated prose are **claims to verify**,
never proof. A statement becomes evidence only when it is anchored to a
verifiable reference: a commit SHA, a PR head, a command with its exit code and
output location, or a pinned artifact with a checksum. The Independent Repair
Reviewer is required to re-check the exact remote head and validate semantic
claims against code and evidence before any verdict is recorded.

## Boundary enforcement in v0.1.0

- No code path in this repository pushes, merges, or writes to a target repository.
- No code path invokes a model, schedules jobs, or selects repositories.
- The validator rejects artifact paths that escape the repository.
- The three-role workflow is advanced manually by a human operator.
