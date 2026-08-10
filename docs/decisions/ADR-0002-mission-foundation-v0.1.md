# ADR-0002 — Mission Foundation v0.1 (formulation/execution boundary)

Status: accepted (Issue #23, draft PR).

## Context

Federation HQ has a proven execution layer (bounded mission → Operator →
Scout → Repair → Review → Gate → Integrator → terminal run) but the layer
before and after it (signals → decide → formulate bounded mission → assess
completed run → remember disposition) is informal. Before automating a
future Mission Director, define its contracts so the Director can be
replaced independently without changing the execution pipeline.

## Decision

Define a schema-first foundation slice:

1. `contracts/mission/mission-candidate.schema.json` — possible work before
   an executable mission exists; explicit facts and enums; no LLM
   self-scoring; `no_mission_warranted` is a first-class healthy disposition.
2. `contracts/mission/mission-contract.schema.json` — the boundary input to
   the Operator; describes the problem, not the repair; represents
   `mission_rejected` (framing invalid) distinctly from "Scout found no
   defect"; `bounded_scope` is declared guidance (`scope_enforcement:
   declared`), never documented as mechanical enforcement.
3. `contracts/mission/run-assessment.schema.json` — terminal facts only;
   no confidence/self-congratulation fields by design.
4. `contracts/mission/mission-ledger.schema.json` + `mission/ledger.yaml` —
   smallest repository-native persistent state; immutable `signal_id`
   identity with evidence-backed aliases; no semantic rename detection.
5. `docs/HQ_MISSION_POLICY.md` — single canonical hand-maintained policy
   (POL-01..15) a future Director must read.
6. `docs/MISSION_DIRECTOR_BOUNDARY.md` — architecture note, signal identity,
   prompt composition contract (canonical role prompt + MissionContract +
   Run Manifest + active assignment + accepted artifacts), Director ≠
   Scout ≠ Operator ≠ Reviewer, one execution path (Recon Missions go
   through the existing Operator).
7. Retroactive validation: the three most recent autonomous runs
   (federation-hq#17 Pilot 03, #19 Stabilization 01, #21 Stabilization 02)
   projected into the new representations as NON-CANONICAL fixtures;
   negative fixtures (no_mission_warranted, wont_fix reopen guard, wrong
   framing → mission_rejected, duplicate).

## Consequences

- Mission formulation and execution are separable; a future Director can be
  built (or replaced) without changing the existing pipeline.
- The system can explicitly decline work (`no_mission_warranted`,
  `mission_rejected`, `duplicate`, `wont_fix`) — a Director that always
  produces work is contrary to policy.
- No runtime behavior changes: no new Gate/Operator/orchestration behavior,
  no enforcement engine, no scheduler, no database. Runtime consumption of
  these contracts is a later bounded slice.
- Historical run artifacts are untouched; new artifacts are additive.

## Non-goals (this slice)

No hq-director, no Director prompt/agent, no candidate ranking, no repo
crawling/recon, no scheduler/heartbeat/daemon/webhook, no LLM scoring, no
vector/semantic infrastructure, no mission queue, no SQL database, no fleet
rollout.

## Sequencing (future, evaluated separately)

1. schema foundation (THIS)
2. Operator mission-rejection / runtime consumption
3. manually authored MissionContracts used in real runs
4. hq-director v0.1
