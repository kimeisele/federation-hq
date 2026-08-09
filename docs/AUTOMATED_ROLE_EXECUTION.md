# Automated Role Execution v0.1 — OMP Reference Adapter

Status: implemented per Issue #15 (draft PR, not yet merged).

## Thesis

**Federation HQ protocol ≠ execution harness.**

The canonical protocol (`docs/COORDINATION_PROTOCOL.md`,
`docs/REPAIR_PIPELINE.md`, `contracts/`, `prompts/registry.yaml`) defines
run state, assignment semantics, role identity, immutable prompt pins,
artifacts, SHA-bound acceptance, the Review Gate, and integration
invariants. It never names, depends on, or records any execution harness,
and it is complete without one: posting an `assignment` comment and waiting
for an external actor remains a fully supported mode.

**Role separation ≠ human-launched process separation.**

Historically, "role separation" was realized by a human launching each role
as a separate process/agent. That was an implementation detail, not a
protocol invariant. The invariant is that role contexts are logically
isolated: Repair and Review run in distinct worker contexts, Review never
inherits Repair's private conversational reasoning, and the information
boundary between roles is the canonical GitHub artifacts plus the exact
reviewed remote head.

Pilot Run 02 (Issue #13,
`runs/run-20260809-agent-city-actionverb-contract-drift/`) demonstrated
that an OMP Operator session can itself launch isolated worker subagents,
observe their protocol submissions, and continue the canonical loop with
zero human role handoffs. This slice codifies that as an **optional,
documented Operator capability** — not as a protocol change.

## Layering

| Layer | Owns | Must NOT |
|---|---|---|
| Federation HQ Core (protocol) | run state, assignment semantics, role identity, immutable prompt pins, artifacts, acceptance, Gate, integration invariants | depend on any harness; expose harness fields in canonical artifacts |
| Harness adapter (e.g. OMP) | starting an isolated worker session; handing it the Coordination Issue / assignment reference; waiting for / observing completion | change protocol semantics; become part of the protocol vocabulary |
| Operator prompt release | the dispatch decision procedure (adapter available → dispatch; else → manual fallback) | perform role work |

The canonical layer is unchanged by this slice. `operator@0.1.0` is
immutable; the new behavior lives in the additive release `operator@0.2.0`
(`prompts/operator/v0.2.0.md`, registered in `prompts/registry.yaml`).
Run manifests pin prompt versions, so existing runs (Pilot 01, Pilot 02,
and any future run pinning `operator@0.1.0`) remain valid and
interpretable.

## OMP as the first reference adapter

OMP is a concrete execution harness with an existing subagent capability.
This repository ships thin project-local role wrappers under `.omp/agents/`
(`hq-scout.md`, `hq-repair.md`, `hq-review.md`, `hq-integrator.md`) that
define ONLY the adapter-side contract:

- read the Coordination Issue and the active assignment (by message
  identity, not a rewritten summary);
- read the run manifest;
- load the exact canonical prompt version pinned for the role;
- execute only that role;
- submit through the existing Issue protocol;
- stop.

The wrappers deliberately do NOT duplicate the canonical Scout/Repair/
Review prompts: `prompts/` remains the single source of semantic role
truth. The wrappers are harness-local files, not canonical artifacts; the
validator does not treat them as such.

An OMP Operator session uses its native subagent/task capability to launch
a worker with the corresponding wrapper. Federation HQ Core does not know
OMP exists.

## Dispatch decision (Operator, per operator@0.2.0)

```text
active worker assignment in canonical state
  ├─ compatible execution adapter available
  │     └─ launch ISOLATED worker (role wrapper)
  │           ├─ worker submits via Issue protocol → validate → canonicalize
  │           │   → artifact_acceptance → next assignment (repeat)
  │           └─ dispatch fails / worker vanishes → retry or fall back to
  │               manual dispatch; otherwise record blocked with the cause
  └─ no adapter available
        └─ external/manual dispatch (historical default, always supported)
```

The Operator remains the ONLY orchestrator. Workers never accept their own
artifact, never advance state, never publish semantic Gate approval, and
never merge (merging is the distinct Integrator capability, itself a
mechanical role around already-approved state). A failed dispatch is an
operational failure, never semantic acceptance: it must become a visible
blocker (or a manual fallback), not a fabricated submission.

## Execution receipt

Recon decision for v0.1: **omit** a separate execution ledger. GitHub Issue
history already provides attributable auditability (message IDs, timestamps,
actors), and harness identity must not affect whether a repair/review
artifact is semantically valid. If a concrete debugging need appears in a
later slice, a minimal non-canonical receipt (execution_id, adapter, role,
assignment_message_id, status, started_at, finished_at) can be introduced
then — it would remain operational only.

## Independence invariant (enforced by the adapter + wrappers)

1. Repair and Review are distinct worker executions (distinct contexts).
2. Review never inherits Repair's private reasoning.
3. The information boundary is canonical artifacts + exact reviewed head.
4. A Review worker cannot publish/merge as Operator merely because
   execution is automated.

## Non-goals (unchanged)

No adapter discovery/marketplace, no remote worker fleet, no concurrency
scheduler, no retry engine, no persistent job database, no
Kubernetes/supervisor, no webhooks, no GitHub Actions orchestrator, no
model marketplace, no DAG engine, no fleet rollout, no Pilot Run 03. OMP
reference execution only; manual fallback remains the baseline.

## Manual/external dispatch fallback

Unchanged from v0.1.0 semantics: the Operator posts the `assignment`; an
external actor or agent notices it and executes the role; the submission
arrives through the Issue protocol exactly as an adapter-launched worker's
would. Both paths are byte-identical from the protocol's point of view.

## Testing posture

Focused content/contract tests (`tests/test_role_automation.py`) prove the
architecture surface: registry release integrity for `operator@0.2.0`;
recognition criteria for dispatchable assignments; worker receives the
Issue/assignment reference rather than a semantic summary; role prompt
resolution from the run's canonical pin; wrapper role boundaries
(self-accept, state-advance, publish, merge prohibitions); repair/review
execution separation; adapter-unavailable fallback; execution-failure
visibility; prior run records and Gate behavior unchanged (full validator +
suite green).
