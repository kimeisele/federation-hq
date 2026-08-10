# ADR-0004 — HQ Director v0.1 (bounded mission selection and formulation)

Status: accepted (Issue #33, draft PR). ADR-0002 step 4.

## Context

Steps 1–3 of ADR-0002 are integrated (Mission Foundation, Operator
MissionContract consumption, real MissionContract-native Pilot, canonical
terminal feedback). The missing Phase -1 role — deciding what bounded work,
if any, is worth doing next — was previously performed informally. This ADR
implements it as a bounded semantic mission-formulation role with a
canonical prompt and OMP adapters.

## Decision

1. **Role boundary:** one Director invocation = one decision cycle; at most
   one MissionContract per cycle; selecting none is a valid outcome; no
   autonomous infinite generation (POL-14). The Director never performs
   Scout/Repair/Review/Operator work and never publishes the Review Gate.
   The Director never performs target/run-record integration and never acts
   as the repair Integrator. The sole merge exception in v0.1 is the NORMAL
   merge of its own validated Federation HQ formulation PR, required to
   canonicalize MissionCandidate + MissionContract + Ledger state before
   Operator handoff — no admin bypass, no force, no Gate publication.
2. **Inputs:** a finite explicit signal set (existing `signal_ref`
   vocabulary) + exact canonical `docs/HQ_MISSION_POLICY.md` +
   `mission/ledger.yaml` (+ canonical terminal RunAssessments where
   relevant). No new Signal database/schema.
3. **Cheap evidence only:** structured signal fields, Ledger, canonical
   RunAssessment facts, exact referenced Issue/PR status, overlap metadata,
   exact policy/artifacts. Deep recon is forbidden to the Director; a bounded
   Recon Mission routes through the Operator path (one execution path).
4. **No self-scoring:** no risk/priority/confidence/importance, no
   embeddings/vector search/ranking engine. Ambiguous eligibility →
   `BLOCKED — ambiguous mission selection` (v0.1 tie boundary).
5. **Selection semantics:** POL-01..07, 10, 11, 14, 15; terminal prior
   Ledger dispositions never silently reopened (POL-04 override required).
6. **Outputs:** only the existing canonical artifact shapes
   (`missions/<mission-id>/mission-candidate.yaml` +
   `mission-contract.yaml`); no new DirectorDecision schema. Non-mission
   outcomes use existing candidate dispositions. Ledger mutation only for
   REAL decisions; tests/smoke use fixtures + temporary/in-memory Ledger
   state.
7. **Canonical prompt + adapters:** `director@0.1.0` registered in
   `prompts/registry.yaml` (released, SHA-pinned); `.omp/agents/hq-director.md`
   (spawns only `hq-operator`); `.omp/agents/hq-operator.md` (spawns only
   the four execution workers; canonical role stays `operator@0.3.0`).
   Handoff = canonical references only; the Operator independently performs
   `operator@0.3.0` admission (may return `mission_rejected` before Scout).
8. **Admission time ordering:** the POL-04 admission Ledger basis B is the
   exact pre-formulation HQ main commit, hashed BEFORE any formulation
   mutation; the formulation merge yields commit C; the Operator handoff is
   Candidate@C + Contract@C + AdmissionLedger@B (mixed-commit pins
   expected). `mission_input.admission_ledger` must pin B, never C/HEAD/
   working-tree/branch.
9. **No real Director mission in this slice:** no live `missions/` package,
   no target mutation; the first live Director-selected Pilot is a separate
   step after review/merge.

## Consequences

- The Director→Operator handoff is structurally supported with zero human
  role-dispatch steps (human starts the cycle once).
- Decision-making is attributable to concrete policy/evidence, not
  self-scoring.
- No new mission artifact schemas; existing schemas remain the single
  contract.

## Non-goals

No crawling, scheduled cycles, heartbeat, daemon, cron, webhook, mission
queue, SQL, vector DB, embeddings, self-scoring, generalized ranking,
multi-mission DAG, parallel missions, Director memory database, new repair
pipeline, new Review Gate, new artifact schemas.
