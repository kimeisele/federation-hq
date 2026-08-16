# Federation Bootstrap Program — Architecture & Program Brief

**Artifact A.** Phase 2 planning output. Suitable for committing to `kimeisele/federation-hq`.

Status: planning only. No code, no PRs, no repository changes.
Date: 2026-08-10.
Basis: Round-1 architecture review + re-verification of all repository facts this plan materially depends on + independent verification of the runtime candidate.

Marker convention: `[FACT]` = verified at the commit inspected. `[REC]` = recommendation. `[OPEN]` = genuinely unresolved.

---

## 1. Executive summary

The federation already has more of this system than it appears to. FAW provides signed delegation, bounded authority, budgets, deadlines, replay protection, pending binding, verifiable receipts, and a proven transport boundary. Federation HQ provides a role-separated, evidence-gated execution path with versioned prompts and machine-checked contracts. What is missing is a single seam — between a verified delegation and a real autonomous execution — plus one security correction that must land before any autonomous credential exists.

This program therefore does **not** build an Agent OS. It proves one vertical path and stops:

```
independent node → verified FAW delegation → dedicated relay → one Actions job
→ ephemeral runner → headless coding runtime → bounded branch result
→ normalized usage/evidence → signed terminal receipt → issuer verifies and closes
```

Three findings shape everything below.

1. **The executor seam is smaller than the prior analyses assumed.** `[FACT]` `CapabilityExecutor.execute(delegation, workdir) -> receipt` in `federated-agent-web` already enforces the deadline (emitting `timed_out`), enforces `authority.filesystem_scope.read_paths`, verifies input digests, writes artifacts, computes `usage`, and constructs the terminal receipt. It is one class in `demo.py` away from being the runtime adapter interface. This is a registry refactor, not a new subsystem.

2. **Cost budgets cannot be honestly enforced at bootstrap.** `[FACT]` OpenHands' own benchmark repository documents cost-tracking failures where `metrics.accumulated_cost` reported $0.00 against $7–13 of actual spend per instance. Combined with FAW's `budget.unenforceable` rejection semantics, this has a hard consequence: the first delegation **must omit** `max_cost_usd` and `max_tokens`, or `verify()` will reject it. Budget enforcement in v1 is wall-clock and output-size only.

3. **Federation HQ's contracts are single-repository by schema.** `[FACT]` `target_repository` is a required, singular string in `run-manifest`, `mission-candidate`, `mission-contract`, and `run-assessment`. Multi-repository missions are structurally impossible without a schema change. The "one bounded semantic change per PR" rule is therefore already mechanically enforced — the Director cannot violate it even by accident. This is the strongest available protection against cross-repository spaghetti, and the program is built to exploit it.

---

## 2. Verified current state

### 2.1 `federated-agent-web` — mature, protocol-ready

`[FACT]` Inspected at `0864a1a`.

- `Transport` ABC (`transports/base.py`) is already correct for this program: `send` / `poll` / `ack(one)` / `nack(one)`, with the docstring rule "never clear the whole outbox" and "partial delivery failure must preserve every unacknowledged message."
- `NadiTransport(state_root, node_id, relay_address, routes, backend)` implements FAW-node-ID → relay-address routing with `.msg`/`.meta`/`.ready` commit-marker staging. Backend is injectable.
- `CapabilityExecutor` is hardcoded to `hash_file` and lives in `demo.py`. `NodeRunner` hardcodes `VerificationPolicy(allowed_actions={"hash_file"}, allowed_external_effects=frozenset({"none"}))`.
- Schemas already express everything a coding runtime needs, with **no `spec_version` change required**: `authority.filesystem_scope` / `network_scope` / `external_effect_scope`, `expected_output.expects_repository_mutation`, `budget.{max_wall_seconds,max_tokens,max_cost_usd,max_output_bytes}`, `receipt.usage`, `receipt.artifacts[]`, `receipt.evidence[]`.
- `verify.py::_evaluate_authority_and_budget` rejects `max_tokens` / `max_cost_usd` with `budget.unenforceable` when local policy declares it cannot measure them. Verification is admission-time; enforcement is the executor's job.
- Project rules: one slice per PR, decisions as ADRs, **agents propose / humans merge**, and an explicit roadmap exclusion of "automated pull requests against foreign repositories" and "Agent City / Agent World integration".
- Python target: 3.11.

### 2.2 `steward-federation` — transport, with defects, plus one security hole

`[FACT]` Full history inspected: 98,314 commits, ~1,400/day sustained, 731 MB server-side (API `size`; ~570 MB local `.git` at inspection), 110 mailboxes, 11,062 messages.

- `NADI_BUFFER_SIZE = 144` ring buffer; 54 of 110 mailboxes sit at the cap and are silently evicting.
- `sync()` still contains `if stats["pushed"] > 0: self.transport.clear_outbox()` while `push_to_hub` swallows per-target exceptions.
- `self._processed = set(list(self._processed)[-2500:])` truncates an unordered set; dedup key is `(source, timestamp)` on a float.
- Node identity is `ag_<hash(public_key)>` — key rotation changes identity.
- Nine mailboxes literally named `*` hold ~1,300 undeliverable messages.
- `HUB_REPO = "kimeisele/steward-federation"` — star topology.
- **Security (as of 2026-08-10):** `agent-city`, `agent-internet`, `agent-world`, and `steward-federation` heartbeats all `curl` unpinned `nadi_kit.py` from `main` and execute it with `FEDERATION_PAT` in the environment. **Corrected 2026-08-16 — see §6 update and the consumer inventory below: this list was incomplete.**

**Consumer inventory (corrected 2026-08-16).** The original four-repo list was wrong. An org-wide scan (all kimeisele repositories, 2026-08-16) found **16 nadi_kit consumer repositories**, reproducible in three stages: (1) `.github/workflows/*.yml` fetches/clones/installs of `nadi_kit`; (2) `.py` files importing `nadi_kit`; (3) `pyproject.toml` dependencies, plus a vendor check for an in-repo `nadi_kit.py`. The 16: `agent-city`, `agent-internet`, `agent-world`, `agent-music`, `agent-red-team`, `agent-village`, `federation-hq`, `agent-template`, `agent-template-acceptance-node-02/03/04/05`, `agent-template-proof-node-01`, `agent-research`, `steward-protocol`, and `steward-federation` itself (source). `agent-arena` vendors `nadi_kit.py` 0.1.0 directly (no fetch, no drift; owner decision 2026-08-16 to leave it until it needs 0.1.2). `mahaclaw`, `vibe-agency`, `steward-test` are relay-only (no `nadi_kit` execution).

### 2.3 `agent-template` — node infrastructure, zero FAW

`[FACT]` No occurrence of `faw`, `federated-agent-web`, or `delegation` in any file. Provides identity, discovery, authority feeds, agent card, governance validation, Nadi wiring, and `scripts/heartbeat_workflow_guard.py` (a real secrets-presence gate). Heartbeat declares `permissions: contents: read`. Python target 3.11.

### 2.4 `federation-hq` — correct governance, single-repo contracts

`[FACT]`

- `docs/BOUNDARIES.md`: HQ is not the runtime, holds no target authority, and "never commits to, opens PRs in, or merges into a target repository in v0.1.0."
- `docs/MISSION_DIRECTOR_BOUNDARY.md`: Director = formulation, Operator = execution; `Director ≠ Scout ≠ Operator ≠ Reviewer`; deep inspection must go through a bounded Recon Mission on the Operator path.
- Contract schemas require singular `target_repository`. `run-manifest` carries `mission_input`, `baseline_sha`, `prompt_pins`, `pipeline_state`.
- `MissionContract` requires `bounded_scope`, `hard_constraints`, `stop_conditions`, `expected_allowed_outcomes`, `policy_reference` + `policy_version` + `policy_sha256`. `scope_enforcement` is `declared` — prose, not mechanical.
- Policy: POL-03 one bounded work item per mission; POL-07 no unnecessary architecture expansion; POL-11 role separation mandatory; POL-14 no autonomous infinite work generation; POL-15 no LLM self-scoring as evidence.
- `prompts/registry.yaml` pins each version by sha256. Scout v0.2.0 (2026-08-10) is MissionContract-native.
- Six completed runs, **all targeting `agent-city`**. No multi-repository precedent.

### 2.5 Runtime candidate — verified independently

`[FACT]` Verified against PyPI, the OpenHands CLI repository, and official docs.

| Property | Finding |
|---|---|
| Licence | MIT |
| Package / version | `openhands` on PyPI, 1.16.0 (2026-05-08) |
| Python | **Requires `==3.12.*`** — FAW and agent-template target 3.11 |
| Headless | `openhands --headless -t "..."` / `-f file` |
| Machine-readable output | `--json` → JSONL event stream (`action` / `observation` events) |
| Approval gate | **Headless always runs in always-approve mode. `--llm-approve` is unavailable. This cannot be changed.** |
| Configuration | Env-based: `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`, `--override-with-envs`; `RUNTIME` / `SANDBOX_VOLUMES` for sandboxing. No `--model`, `--max-iterations`, `--workspace`, or `--sandbox` flags. |
| Server/UI assumption | None for headless; `serve` / `web` / GUI are separate opt-in subcommands |
| Cost/token accounting | `Metrics` with `accumulated_cost`, `TokenUsage`. **Demonstrably unreliable** — OpenHands' own benchmarks issue #603 (2026-04-02) records agent types reporting `$0.00` against $7–13 real spend |
| Maintenance posture | **"OpenHands V1 CLI is feature-complete and primarily maintained for stability. Expect only major bug fixes… new features are unlikely."** Active development is on the SDK. |

**Verdict:** acceptable as the first adapter. No material blocker, so no alternative survey is performed. Three consequences are carried into the program: containment must be entirely external (no internal approval gate); cost claims must not be trusted (§5, D5); and the CLI's maintenance posture means the adapter boundary must be genuinely narrow so the SDK can replace it later (§10 residual).

`[UNVERIFIED]` Cost magnitudes cited for calibration planning (trivial run $0.05–0.30; real fix $0.50–3; long autonomous run $5–30) come from a community deep-dive, not an official source. Treat as order-of-magnitude only; PROGRAM 6 replaces them with measurement.

---

## 3. Target architecture

```
GITHUB — durable control plane
  repos = node identity | issues = bounded work | PRs = proposed change
  Discussions = coordination (DEFERRED, §12)
  write budget: single-digit commits per attempt, never per agent step
        │
        ▼  event / cron / dispatch
GITHUB ACTIONS — coarse job launcher
  one job = one attempt
        │
        ▼
EPHEMERAL RUNNER — all high-frequency execution
  LLM calls, shell, edits, tests, tool loops, intermediate state
  nothing intermediate is committed anywhere
        │
   ┌────┴─────────────────────────────┐
   │ FAW — contract in, receipt out   │
   │ identity, authority, budget,     │
   │ deadline, replay, evidence       │
   └────┬─────────────────────────────┘
        ▼  only where durability is required
NADI — dumb cross-node delivery (dedicated relay for v1)
```

**First-class invariant:** one delegation in → one terminal receipt out → minimal durable commit boundaries per attempt. Intermediate agent actions become `receipt.evidence[]` and `receipt.artifacts[]` digests, never commits and never Nadi messages.

---

## 4. Non-goals

Not in this program, at all:

- redesigning the FAW wire protocol, schemas, canonicalization, or `spec_version`
- rewriting Nadi or designing a second message protocol
- building an agent harness
- multi-runtime support (build one adapter, not two)
- a new software implementation or package repository; the dedicated relay
  and sandbox target in D1/D2 are explicit setup surfaces, not new subsystems
- Redis / Kafka / Kubernetes / a custom queue / a permanent server
- a web UI
- Discussions as part of the correctness kernel
- pulling `agent-city`, `agent-world`, `agent-internet`, `steward`, or `steward-protocol` into the vertical slice
- rehabilitating the shared Nadi hub as a prerequisite
- Agent City / Agent World integration (also excluded by FAW's own roadmap)

---

## 5. Decisions and defaults

For each: consequence → conservative default → whether the program can proceed before a human changes it.

### D1 — Automated PRs against foreign repositories

**Consequence.** FAW's roadmap explicitly excludes "automated pull requests against foreign repositories," and its working rules state "agents propose, humans merge… violating it in our own repo would be the loudest possible signal that the principles are decorative." FAW's entire credibility argument is built on that restraint. Silently overriding it would cost more than the feature is worth, and would also require a PR-write credential that materially widens blast radius.

**Default `[REC]`.** The runtime's terminal artifacts are: **an isolated branch, commits, artifacts, and a signed receipt. No PR is opened by any agent.** The attempt is complete when the branch exists and the receipt verifies. A human — or later, repository-local trusted automation inside the target repo — performs the PR/merge transition.

**Scope of this rule (sharpened 2026-08-16).** D1 binds the **delegated FAW runtime**: the autonomous executor that receives a signed delegation. It must not open PRs, must not write `main`, and its terminal artifact is the isolated `faw/attempt/<attempt_id>` branch plus the signed receipt. D1 does **not** cover: (a) Federation HQ repair missions, whose `repair-result` schema carries a `pull_request` reference by design and whose Builder opens a PR in the target repository through the HQ Operator path; or (b) direct owner instructions outside the Director cycle. The distinction is the authority behind the write: a runtime acting on a delegation never writes outward; HQ repair and owner-directed work write through an explicit, reviewed path.

Additionally, the first slice targets a **dedicated sandbox repository under the same owner**, so the "foreign repository" question does not arise at all in v1. Same-owner vs foreign is left as a later distinction, made by ADR in FAW when there is evidence to make it with.

**Can proceed without human change: YES.** This default sits strictly inside every existing rule. It also removes `pull_requests: write` from the credential model entirely (D3).

### D2 — Shared hub vs dedicated FAW relay

**Consequence.** Routing signed delegations through the shared hub today means routing them through a 144-message ring buffer that silently evicts, a `sync()` that clears the whole outbox on partial delivery, and nondeterministic replay truncation. An audit trail built on a substrate that drops records is worse than no audit trail. Conversely, making hub rehabilitation a prerequisite couples the vertical proof to a repair project on a live system with four dependent nodes.

**Default `[REC]`.** **Dedicated FAW relay repository for the first slice**, reusing the pattern already proven in the v0.4 controlled rehearsal (`federation-operator/faw-nadi-live-relay`). `NadiTransport` already parameterizes `relay_address`, `routes`, and `backend`, so no code change is needed to point at a different relay. Hub reliability repairs (F2/F3/F4) become a **separate, independently valuable mission track** that does not gate the vertical proof. Convergence only after both sides have evidence.

**Can proceed without human change: YES.**

### D3 — Runtime credentials

**Consequence.** A credential able to write to a repository, held on a runner executing agent-authored code, with no internal approval gate (§2.5). This is the primary blast-radius surface of the entire program.

**Default `[REC]`.** Minimum-privilege model:

| Concern | Default |
|---|---|
| GitHub identity | GitHub App installation, **not** a PAT |
| Installation scope | Exactly two repositories: the dedicated relay + the sandbox target |
| Permissions | `contents: write` only. **No** `pull_requests: write` (follows from D1). No `actions`, `secrets`, `admin`, or org scopes |
| Branch namespace | `faw/attempt/<attempt_id>` only; `main` protected and never a push target |
| LLM secret | Actions secret, injected **only** into the runtime step's env; never present during checkout of task-supplied content |
| Workflow permissions | `permissions:` declared at job level, default `contents: read`, elevated only in the step that publishes the branch |
| Untrusted content | Delegation `input` is data. It is written to a file and passed via `-f`; it is never interpolated into a shell command or a workflow expression |
| Concurrency | Actions `concurrency: group: faw-attempt-<attempt_id>`, `cancel-in-progress: false` |

**Can proceed without human change: YES for design.** App installation and secret creation are owner/operator setup outside Director mission formulation. They are not part of PROGRAM 0a–0d and occur only at the later credential/setup gate.

### D4 — `steward-federation` scope

**Consequence.** Converting a 98,314-commit live relay into a general runtime package couples the runtime's release cycle to a repository whose `main` is already an executable supply-chain surface for four nodes (§2.2). That is the exact coupling this program is trying to remove.

**Default `[REC]`.** **`steward-federation` stays transport-only.** The executor seam and pure `RuntimeResult` type land in `federated-agent-web`, next to the verification core they serve. The runtime adapter and node wiring land in `agent-template`. **No new software implementation or package repository is created.** If a runtime package boundary later proves itself, extraction is cheap; creating it now would be a boundary asserted rather than demonstrated (and would violate POL-07).

**Can proceed without human change: YES.**

### D5 — Cost ceiling

**Consequence.** This one has a hard, derived constraint rather than a preference. `verify.py` rejects a delegation carrying `max_cost_usd` when local policy sets `can_enforce_cost=false`, and the same for `max_tokens` / `can_enforce_tokens`. Given that OpenHands' own project documents `accumulated_cost` reporting `$0.00` against real spend of $7–13, setting those flags true at bootstrap would be a false claim embedded in a cryptographically signed audit trail — precisely the failure mode FAW exists to prevent.

**Default `[REC]`.**

- Node policy declares `can_enforce_tokens = false`, `can_enforce_cost = false`.
- **The first delegation therefore omits `max_cost_usd` and `max_tokens` entirely.** It carries `max_wall_seconds` and `max_output_bytes` only. Both are enforceable: FAW already implements deadline → `timed_out`, and output size is directly measurable.
- Runaway spend is bounded out-of-band, not in the contract: `MAX_ITERATIONS` (default ~100), `LLM_NUM_RETRIES`, an Actions `timeout-minutes` kill, and a **hard spend cap configured at the LLM provider**.
- **PROGRAM 6 is a mandatory calibration slice**: ≥10 runs of the same bounded task, recording actual wall seconds, Actions minutes, provider-reported tokens, provider-reported cost, and runtime-reported cost, so the divergence is measured rather than assumed. Only after that may a later mission propose flipping `can_enforce_cost`.

**Can proceed without human change: YES**, and it constrains the E2E gate (§10, step 8).

---

## 6. Security prerequisites — pre-runtime gate

Every item below must hold **before** the first autonomous runtime credential exists. Scope is deliberately limited to obvious autonomous-agent blast radius; this is not an enterprise security program.

| # | Control | Status |
|---|---|---|
| S1 | No node executes mutable remote code. `curl … main/nadi_kit.py` replaced by a commit-SHA-pinned fetch with digest verification, or a vendored copy | **Satisfied 2026-08-16.** Org-wide scan: 0 unpinned `main` fetches; all 16 consumers pin `nadi-kit @ v0.1.2` (tag commit `03008a5a`, blob `47d8e3bb…`) or fetch the raw tag; `agent-arena` vendors 0.1.0 as a deliberate exception. Evidence: Block-A PRs (federation-hq#65, agent-template#25, agent-template-acceptance-node-02/03/04/05, agent-template-proof-node-01#343, agent-red-team#376, agent-village#406, agent-city#2721, agent-world#1210, agent-music#375) plus earlier slices. |
| S2 | Runtime credential is a scoped GitHub App, not a PAT (D3) | Blocking |
| S3 | `contents: write` only; no `pull_requests` permission (follows D1) | Blocking |
| S4 | Writes confined to `faw/attempt/<attempt_id>`; `main` branch-protected on the sandbox target | Blocking |
| S5 | LLM secret injected only into the runtime step; absent from checkout and from any step handling delegation input | Blocking |
| S6 | Delegation `input` never interpolated into shell or workflow expressions; passed as a file | Blocking |
| S7 | Runner is ephemeral and disposable; nothing persists between attempts except what the receipt references | Blocking |
| S8 | Target repository allowlist enforced in the adapter, not only in configuration | Blocking |
| S9 | Deadline kill: Actions `timeout-minutes` ≤ delegation `max_wall_seconds`, plus in-adapter abort | Blocking |
| S10 | Artifact and evidence size bounds enforced against `max_output_bytes` before receipt construction | Blocking |
| S11 | Cost/token claims in `receipt.usage` are either measured or omitted — never asserted unverified (D5) | Blocking |
| S12 | Bounded network policy where practical | Best-effort; not blocking for v1 |

S1 is the single highest-value item in this program and is independent of everything else. It should be formulated first.

---

## 7. Repository ownership map

| Repository | Role in this program | In scope? |
|---|---|---|
| `federated-agent-web` | Executor/capability seam; the pure `RuntimeResult` type; deadline/budget integration; receipt construction from execution evidence. **No new dependencies.** | **Yes** — PROGRAM 2 |
| `agent-template` | The runtime adapter itself; FAW integration; Actions entrypoint; runtime bootstrap; secrets contract; one executable capability | **Yes** — PROGRAMS 3, 4 |
| `steward-federation` | S1 security correction only. Reliability repairs are a separate track | **Yes, narrowly** — PROGRAM 0 |
| dedicated FAW relay | Transport surface for the first slice (D2). Uses existing `NadiTransport` unchanged | **Yes** — PROGRAM 5 |
| sandbox target repo | Destination of the bounded mutation (D1) | **Yes** — PROGRAM 6 |
| `agent-city`, `agent-world`, `agent-internet` | S1 consumer pinning only | **Yes, narrowly** — PROGRAM 0b/0c/0d |
| `steward`, `steward-protocol` | None | **No.** Enter only if a Scout finding demonstrates a verified dependency |

---

## 8. Program dependency graph

```
PROGRAM 0 ── S1 security ───────┐
  (four repository-scoped pins)│  blocks only P4+
                                │
PROGRAM 1 ── recon ─────────────┼──► PROGRAM 2 ── FAW executor seam
  (bounded, per repo)           │         │      [federated-agent-web]
                                │         ▼
                                │    PROGRAM 3 ── runtime adapter
                                │         │      [agent-template]
                                │         │      (offline, no credentials)
                                └─────────┤
                                          ▼
                                     PROGRAM 4 ── agent-template node
                                          │       (first credential use)
                                          ▼
                                     PROGRAM 5 ── relay transport path
                                          │
                                          ▼
                                     PROGRAM 6 ── E2E proof + calibration
                                          │
                                          ▼
                                     PROGRAM 7+ ── deferred (§12)
```

Note the sequencing win: **PROGRAMS 2 and 3 require no credentials at all.** They are exercised against the filesystem transport and a stubbed runtime. Security work (P0) and seam work (P2/P3) therefore have no dependency on one another, and the credential only becomes necessary at P4. This is a dependency observation, not permission for the Director to schedule parallel cycles: each Director invocation still selects at most one MissionContract.

OMP is an optional reference execution harness around the canonical HQ Issue,
artifact, and role protocol. It is not part of FAW, the MissionContract, the
Ledger, or the run-artifact semantics. The existing manual/external dispatch
path remains valid when OMP is unavailable.

---

## 9. Evidence-gated stages

Each stage is one or more HQ missions. Because `target_repository` is singular by schema, **each stage below maps to at least one mission per repository** — the Director cannot combine them.

---

### PROGRAM 0 — Security prerequisite

**Objective.** Remove mutable remote code execution from the federation through four repository-scoped missions. Credential, relay, and sandbox creation are separate setup actions and are not performed by mission 0a.
**Target repository.** `kimeisele/steward-federation` for mission 0a.
**Why.** S1 is a live federation-wide compromise path; introducing autonomous coding credentials before fixing it multiplies it. Independent of every other stage.
**Dependencies.** None. Start here.
**Director.** Formulate 0a as a bounded security mission with an explicit `hard_constraints` entry forbidding any change to Nadi message semantics, buffer behaviour, or `sync()` — this mission is *only* about how the code is obtained.
**Scout.** Inspect only `steward-federation`'s self-fetch and report its exact file, line, source binding, and credential exposure. Do not propose reliability fixes or inspect consumer repositories in this mission.
**Builder.** Remove the mutable self-fetch and execute `nadi_kit.py` from the workflow's already pinned repository checkout. Smallest robust correction only.
**Reviewer.** Verify the workflow executes only the checked-out head; verify no semantic change to `nadi_kit.py` behaviour; verify against the actual remote head.
**In scope.** `.github/workflows/hub-heartbeat.yml` and, only if needed, one focused guard under `tests/`.
**Out of scope.** F2 ring buffer, F3 outbox clearing, F4 replay truncation, F6 `*` mailboxes, hub topology.
**Acceptance evidence.** `steward-federation`'s workflow contains no runtime fetch of mutable `main/nadi_kit.py`; a focused guard fails when that fetch is reintroduced; the existing `sync → heartbeat → sync` order and relevant tests remain unchanged; the exact commit SHA is recorded.
**Abort.** If pinning breaks a running node's heartbeat and cannot be resolved within the mission's bounded scope → revert, record, re-formulate.
**Unlocks.** The three consumer S1 missions. PROGRAM 4 remains blocked until all of S1–S11 are satisfied. PROGRAM 0 does not gate P1–P3.

After 0a is accepted, S1 continues as three separate, single-repository missions: **0b** `agent-city`, **0c** `agent-internet`, and **0d** `agent-world`. Each removes its mutable fetch or unpinned dependency without changing heartbeat or Nadi semantics. S1 is satisfied only after all four repository heads pass the cross-repository inventory check.

*Setup items (operator, not missions):* create the GitHub App per D3 and the dedicated FAW relay repository per D2; create the sandbox target repository with `main` protected. These actions remain deferred until the program reaches their credential/setup gate and are not part of mission 0a.

---

### PROGRAM 1 — Repository-grounded recon

**Objective.** Replace assumption with canonical findings for the two repositories that will be modified.
**Target repository.** PROGRAM 1a targets `federated-agent-web`; PROGRAM 1b targets `agent-template` in a later, separate Director cycle.
**Why.** POL-01 requires evidence-backed missions. The Director must not formulate P2–P4 from this brief alone; this brief is context, not evidence.
**Dependencies.** None.
**Director.** Formulate at most one bounded Recon Mission per cycle with `decision_question` set. Formulate PROGRAM 1a first — e.g. "What is the minimum change in `federated-agent-web` that makes the capability executor pluggable without altering `verify()`, the schemas, or `spec_version`?" After its canonical Scout result is accepted, a later explicit Director cycle may formulate PROGRAM 1b for `agent-template`.
**Scout.** Produce canonical findings only. Explicitly permitted to contradict this brief (POL-09).
**Builder / Reviewer.** Not engaged.
**In scope.** PROGRAM 1a: `src/federated_agent_web/{demo,runner,verify,transports}.py`, `tests/`, `docs/adr/`. PROGRAM 1b, in its later cycle: `scripts/`, `.github/workflows/`, `data/federation/`, `pyproject.toml` in `agent-template`.
**Out of scope.** Any code change. Any repository not named.
**Acceptance evidence.** Two Scout artifacts recorded under `runs/`, each anchored to a `baseline_sha`, each answering its `decision_question` with file-and-line references.
**Abort.** If Scout finds the seam is not achievable without a schema or `spec_version` change → **stop the program** and escalate; that invalidates §1 finding 1 and the whole plan.
**Unlocks.** PROGRAM 2.

---

### PROGRAM 2 — FAW executor seam

**Objective.** Make capability execution pluggable. No wire-format change.
**Target repository.** `kimeisele/federated-agent-web`.
**Why.** `[FACT]` `NodeRunner` imports `CapabilityExecutor` from `demo.py` and hardcodes its policy. There is no seam today.
**Dependencies.** PROGRAM 1 recon accepted.
**Director.** One mission. `hard_constraints` must include: no change to `schemas/`, `verify.py` verification order, canonicalization, golden vectors, or `spec_version`; existing conformance suite passes unchanged.
**Scout.** Already delivered in P1.
**Builder.** Extract an executor protocol matching the existing shape — approximately `execute(delegation: dict, workdir: Path) -> receipt_dict` — plus a capability→executor registry; move `hash_file` out of `demo.py` into it; make `NodeRunner`'s `VerificationPolicy` injectable rather than hardcoded. Preserve every behaviour `CapabilityExecutor` already has: deadline→`timed_out`, `filesystem_scope.read_paths` enforcement, input digest verification, `usage` computation.
**Reviewer.** Verify golden vectors and `faw demo` unchanged; verify the conformance suite passes unmodified; verify `hash_file` behaviour is byte-identical.
**In scope.** `src/federated_agent_web/{demo,runner}.py`, new executor module, `tests/`.
**Out of scope.** `verify.py` semantics, schemas, transports, any runtime integration, any LLM.
**Acceptance evidence.** `faw demo` ends `demo: OK`; full test suite green; conformance package green; a second trivial capability registered in tests proves the registry works.
**Abort.** Any required change to signed bytes → stop, escalate to FAW maintainer.
**Unlocks.** PROGRAM 3.

---

### PROGRAM 3 — First runtime adapter

**Objective.** One runtime adapter behind the P2 seam, plus runtime-result normalization. Offline-testable.
**Target repository.** `kimeisele/agent-template`.
**Why.** This is the missing architectural component identified in Round 1. **Placement rationale:** `[FACT]` `federated-agent-web` declares exactly three runtime dependencies (`cryptography`, `jsonschema`, `rfc8785`), no optional-dependency groups, and `requires-python = ">=3.11"`; its README promises the demo needs "no private keys, external services, or networked FAW peers." The runtime requires `==3.12.*` and pulls a full provider stack. Putting the adapter in FAW would break its Python floor, multiply its dependency surface, and change what the reference implementation *is*. The adapter therefore lives in the node, which already owns secrets, workflows, and provider configuration. FAW keeps only the seam and the pure `RuntimeResult` type — zero new dependencies.
**Dependencies.** PROGRAM 2 merged and pinned.
**Director.** One mission. `hard_constraints`: the runtime must know nothing about FAW, Nadi, federation identity, or receipts; adapter must be testable without network or LLM access (stubbed subprocess).
**Scout.** Bounded: confirm the JSONL event contract and exit-code semantics against the installed CLI version; confirm Python 3.12 coexistence with the 3.11 node code.
**Builder.** Implement the adapter: render delegation → task file; invoke `openhands --headless --json -f task.md` with env-based configuration; parse JSONL; enforce deadline abort and `max_output_bytes`; normalize to a `RuntimeResult`; map to `succeeded` / `failed` / `timed_out`. **Omit cost/token fields from `usage` (D5).**
**Reviewer.** Verify the runtime is invoked as a subprocess with no FAW imports; verify a stubbed runtime exercise produces a schema-valid receipt; verify deadline abort actually kills the process.
**In scope.** New `runtime/` module in `agent-template` + tests with a stubbed runtime binary. The `RuntimeResult` dataclass and receipt-construction helpers land in `federated-agent-web` as part of PROGRAM 2, not here.
**Out of scope.** A second adapter. Real LLM calls. Any credential. Any repository mutation.
**Acceptance evidence.** Adapter tests green against a stub; schema-valid receipt produced; deadline abort demonstrated; zero FAW symbols reachable from the runtime process.
**Abort.** If the JSONL contract cannot be parsed reliably enough to determine terminal status → reassess runtime choice before proceeding.
**Unlocks.** PROGRAM 4.

---

### PROGRAM 4 — Executable node integration

**Objective.** `agent-template` gains FAW plus an Actions entrypoint that can run one attempt.
**Target repository.** `kimeisele/agent-template`.
**Why.** `[FACT]` The template has zero FAW integration; this is new integration work, not "adding OpenHands."
**Dependencies.** PROGRAM 3 merged **and** PROGRAM 0 (S1–S11) satisfied. This is the first stage where a credential exists.
**Director.** One mission. `hard_constraints` must restate S3–S9 as mission constraints.
**Scout.** Confirm how `heartbeat_workflow_guard.py` gates optional federation config, and whether the existing `permissions: contents: read` model can be preserved for the heartbeat while the attempt workflow declares its own elevated permission.
**Builder.** Add: FAW node identity/manifest wiring; a `faw-attempt` workflow (`workflow_dispatch` + `repository_dispatch`); runtime bootstrap; the secrets contract; one executable capability declared in `docs/authority/capabilities.json`.
**Reviewer.** Verify branch-namespace confinement, absence of `pull_requests` permission, secret scoping per step, and that the delegation input is never shell-interpolated.
**In scope.** `.github/workflows/faw-attempt.yml`, `scripts/`, `.well-known/`, `docs/authority/capabilities.json`, `pyproject.toml`.
**Out of scope.** Nadi transport wiring (P5). Discussions. Modifying the existing heartbeat's semantics.
**Acceptance evidence.** A locally constructed signed delegation exercises the `agent-template` integration against a disposable or mock target and produces a schema-valid signed receipt as an Actions artifact. It does not write another repository. The first real sandbox branch is created only by the later PROGRAM 6 mission targeting the sandbox repository.
**Abort.** Any secret reachable from a step handling task-controlled content → halt, remediate before continuing.
**Unlocks.** PROGRAM 5. Real sandbox mutation remains deferred to PROGRAM 6.

---

### PROGRAM 5 — Safe cross-node transport path

**Objective.** Delegation and receipt cross the dedicated relay unchanged, between two independent identities.
**Target repository.** `kimeisele/agent-template` (node config) — the relay itself holds no code.
**Why.** D2. `NadiTransport` already exists and is proven; this stage configures rather than builds.
**Dependencies.** PROGRAM 4.
**Director.** One mission. `hard_constraints`: no change to `NadiTransport`; no use of the shared hub; FAW node IDs and relay addresses must differ (ADR 0001).
**Scout.** Confirm the relay backend's authentication path under the App credential.
**Builder.** Configure issuer and executor nodes with distinct FAW identities, distinct relay addresses, and an explicit `routes` map. Wire poll/ack into the attempt workflow.
**Reviewer.** Verify byte-identical document transport in both directions by digest; verify ack tombstones and non-destructive mailbox reread; verify a missing route fails closed and retains the staged message.
**In scope.** Node configuration, relay wiring, transport tests.
**Out of scope.** Any change to `steward-federation`. Hub convergence.
**Acceptance evidence.** Round-trip digest equality both directions; exactly one replay admission; reread yields zero envelopes without deletion — i.e. the v0.4 rehearsal's evidence shape, reproduced under the new credential model.
**Abort.** Any byte mutation in transit → stop; the transport claim is invalidated.
**Unlocks.** PROGRAM 6.

---

### PROGRAM 6 — Vertical E2E proof and calibration

**Objective.** Close the loop, then measure it.
**Target repository.** The dedicated sandbox target. `kimeisele/agent-template` is a merged and pinned harness dependency, not a second target repository. If the harness itself requires changes, those changes are a separate earlier mission targeting `agent-template`.
**Why.** This is the program's reason to exist.
**Dependencies.** PROGRAMS 0–5.
**Director.** Separate cycles against the sandbox target: **6a** E2E proof against §10; after 6a is accepted, **6b** calibration (D5).
**Scout.** For 6b, define the measurement schema before any runs.
**Builder.** 6a: execute the acceptance contract end to end and capture evidence. 6b: repeat the same bounded task ≥10 times, recording wall seconds, Actions minutes, provider-reported tokens, provider-reported cost, and runtime-reported cost.
**Reviewer.** Verify every numbered clause of §10 independently, from committed evidence and Actions logs, without relying on the builder's narrative (POL-15).
**In scope.** The acceptance run and its evidence. Measurement records.
**Out of scope.** Enabling `can_enforce_cost`. Performance tuning. A second capability.
**Acceptance evidence.** §10 satisfied in full, plus a calibration record with a stated divergence between runtime-reported and provider-reported cost.
**Abort.** Steps 5, 10, or 14 of §10 failing → the delegation/receipt semantics are wrong; stop and re-formulate rather than patch.
**Unlocks.** §12 deferred work, on evidence.

---

## 10. E2E acceptance contract

The first task must be intentionally boring. It demonstrates the system boundary, not intelligence. `[REC]` a task such as *"add a docstring to function `f` in `x.py` and leave everything else unchanged."*

A non-author must be able to verify each clause from committed evidence.

1. Node A has a stable FAW identity (`urn:faw:…`) with a manifest chain.
2. Node B has an independent stable FAW identity, distinct from its relay address.
3. Node A registers exactly one outstanding delegation in its pending store **before** transport is used.
4. The signed delegation crosses the dedicated relay with **byte-identical** SHA-256 on arrival.
5. Node B verifies through the full 11-step procedure and **atomically admits it exactly once**.
6. GitHub Actions launches exactly one isolated attempt, under a `concurrency` group keyed on `attempt_id`.
7. The runtime executes the bounded task headlessly inside the ephemeral runner.
8. **Enforced ceilings are `max_wall_seconds` and `max_output_bytes` only.** The delegation carries **no** `max_cost_usd` and **no** `max_tokens` — with `can_enforce_cost=false` and `can_enforce_tokens=false`, including them would be rejected as `budget.unenforceable` (D5). Deadline overrun must produce a `timed_out` receipt, not a hang.
9. The result is a branch `faw/attempt/<attempt_id>` in the sandbox repository. **`main` is not written. No PR is opened by any agent (D1).**
10. Node B produces **exactly one** signed terminal receipt bound to the delegation digest.
11. `receipt.artifacts[]` and `receipt.evidence[]` digests bind the branch head SHA and the runtime log.
12. The receipt crosses back over the same relay with byte-identical SHA-256.
13. Node A verifies the receipt against Node B's pinned manifest chain and atomically transitions the pending record to `terminal`.
14. Re-delivery of the same delegation does **not** execute the task twice; the replay store admits `(issuer, attempt)` exactly once.
15. No maintainer machine participates. Provable from Actions run logs and commit authorship.

---

## 11. Failure and stop rules

**Hard stop — halt the program, escalate:**

- Any stage requires a change to FAW schemas, canonicalization, verification order, or `spec_version`.
- A Scout finding contradicts a `[FACT]` in §2 that a downstream stage depends on.
- A secret is found reachable from a step processing task-controlled content.
- An agent opens a PR or writes to `main` in any repository.
- The receipt/delegation binding or replay guarantee fails (§10 steps 5, 10, 14).

**Stage abort — revert, record, re-formulate:**

- A mission's acceptance evidence cannot be produced within its bounded scope.
- The Reviewer cannot verify a claim against the actual remote head.
- A slice would require touching a repository not in §7.

**Rework limits:** two failed rework cycles on one mission → the mission framing is suspect (POL-10); return to formulation rather than continuing to build.

**The Director must never:** implement target code, perform unbounded source inspection, merge target-repository work, combine repositories into one assignment, or override target-repository governance. Under the canonical HQ Director contract it may normally merge only its own validated Federation HQ formulation PR or Ledger-only decision PR.

---

## 12. Deferred work

Recorded, not scheduled. Each requires evidence from the vertical slice before it is justified.

| Item | Trigger to reconsider |
|---|---|
| Nadi hub reliability repairs (F2 ring buffer, F3 outbox clearing, F4 replay truncation, F6 `*` mailboxes) | Independent track; valuable regardless. Not a prerequisite (D2) |
| Hub / dedicated-relay convergence | After both have operational evidence |
| GitHub Discussions coordination surface | Only after §10 passes **without** Discussions. Smallest form: discussion → work object → delegation reference → receipt/status posted back. Never high-frequency chatter |
| Second runtime adapter (mini-SWE-agent, Steward) | Only if the first adapter's boundary proves inadequate |
| Migration from OpenHands CLI to the SDK | The CLI is in maintenance mode (§2.5); revisit when the adapter is stable |
| Enabling `can_enforce_cost` / `can_enforce_tokens` | After PROGRAM 6b calibration produces a measured divergence |
| Second independent node; broader federation integration | After one node works end to end |
| `agent-city` / `agent-world` / `agent-internet` participation | Only on a verified dependency, never for narrative completeness |
| Hub history size (570 MB `.git`) | When Actions checkout time becomes a measured problem |

---

## 13. Human decision points still genuinely unresolved

Everything in §5 has a conservative default that lets the program start. These four do not.

**H1 — Does the executor seam conflict with FAW's stated architectural commitments?**
`[FACT]` FAW's roadmap excludes "LLM routing" and "Agent City / Agent World integration" from the path to 1.0.

This is **resolvable by repository evidence, not by asking permission** — which is exactly what Scout and Review exist for. The decision rule is:

> Verify whether a runtime-neutral capability-execution seam can be introduced without changing FAW protocol semantics — no schema, canonicalization, verification-order, golden-vector, or `spec_version` change — and without expanding FAW into LLM routing or provider selection. If both hold, **proceed**. If repository evidence shows it would violate an explicit architectural commitment, **stop and escalate**.

With PROGRAM 3's adapter relocated to `agent-template` (§9), the remaining change to FAW is a capability→executor registry plus a pure result type, adding zero dependencies. On the evidence available, that is not LLM routing. The Scout output for PROGRAM 1 should state this explicitly against the roadmap text so the finding is on record rather than assumed.

Residual genuinely-human element: none for v1 under the above rule. It returns only if evidence shows the seam cannot be built without touching signed bytes — which is already a §11 hard stop.

**H2 — Does Federation HQ need a program-level grouping concept?**
`[FACT]` `target_repository` is singular and required across all four contract schemas, and no schema field ties multiple missions into one program.

**Resolved for this bootstrap `[REC]`.** The existing mission Ledger, mission IDs, `related_run_ids`, `last_observed_evidence`, committed run artifacts, and the canonical program-document reference provide sufficient linkage. Stage, dependency, and unlock status belong in bounded evidence text or the coordination Issue. This program does not add `program_id`, a second program database, or a schema change. Reconsider only if an observed retrieval or integrity failure demonstrates that the existing references are insufficient.

**H3 — What is the real budget ceiling per delegation, and who pays?**
Not derivable from any repository. Needed as an input to PROGRAM 6b, not before.

**H4 — Does the dedicated sandbox target repository count as "not foreign" for FAW's rule?**
D1's default avoids needing an answer for v1 by producing no PRs at all. But the moment repository-local automation converts a branch into a PR, the question becomes live. `[REC]` Record it as a FAW ADR when there is evidence, not now.

## 14. Execution record (reconciled 2026-08-16)

This section records how the program has actually run, so the document stays
honest about the difference between the planned path and what happened. It
is a factual record, not a defect report.

**P0 (S1) — completed.** 0a steward-federation (hub-heartbeat executes the
checked-out `nadi_kit.py`), then 0b/0c/0d agent-city/-internet/-world, each
as a single-repository mission with a run record in `runs/`. Completed and
merged 2026-08-13/2026-08-16 (PRs and merge SHAs in the individual run
records). S1 is satisfied org-wide (§6).

**P2 (FAW executor seam) and P3 (first runtime adapter) — completed, but by
direct owner instruction, not through the Director cycle.** `execution.py`
(FAW) and `agent_runtime/` (agent-template) exist at the remote heads and
are merged (FAW #46/#47, agent-template #23). They were implemented per a
direct owner mandate; **the P1 recon missions were not run** — the `[FACT]`
claims in §2 were verified by remote-head inspection and merge-SHA checks
rather than by canonical Scout artifacts. No P1 Scout findings were
retroactively constructed; that would be fabricating evidence for a process
that did not happen. The verification that does exist: the merge SHAs above
and the remote-head checks recorded in this session.

**P4–P6 — not started.** No `faw-attempt` workflow, no dedicated relay
repository, no sandbox target repository exists (verified 2026-08-16).
P4 remains blocked on S2–S4 (owner setup: GitHub App, relay, sandbox).

**Consequence for the program:** stages 2 and 3 are usable as merged,
pinned interfaces (P2/P3 each exist and are tested), but they carry no
Director-cycle acceptance artifacts. A later stage that depends on a §2
claim must re-verify it (POL-01) rather than assume the original `[FACT]`
marking — exactly what Artifact B requires.
