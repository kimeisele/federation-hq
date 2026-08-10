# HQ Mission Policy — v0.1

**Status:** canonical, hand-maintained. **Reference:** `docs/HQ_MISSION_POLICY.md`.
This is the SINGLE canonical policy artifact that mission formulation (human
or future Director) MUST read before deciding whether bounded work is
warranted and before opening a MissionContract. Do not maintain a second
competing policy source.

Normative rules below are numbered (`POL-xx`) and referenced by MissionContract
`policy_reference` and by ledger/candidate dispositions.

## POL-01 — Bounded evidence-backed missions only

A mission may only be formulated from one or more structured signals
(known-red test nodes, CI failures, GitHub Issues, previous RunAssessments,
prior Ledger dispositions, explicit human requests, concrete workflow
failures) and must name the evidence. No open-ended work.

## POL-02 — No "improve this repo" missions

A mission must have a bounded objective / decision question answerable from
the pinned baseline. "Improve this repository", "make things better", and
equivalent unbounded framings are rejected.

## POL-03 — One bounded work item per mission

Each MissionContract carries exactly one bounded objective. Multiple
discrepancies belong in separate missions.

## POL-04 — Prior dispositions checked first

Before opening work, consult the Mission Ledger for the signal(s). A
previous `completed`, `wont_fix`, `no_mission_warranted`, `duplicate`,
`rejected`, or `superseded` disposition blocks a new mission UNLESS new
evidence explicitly supersedes that disposition (new source evidence, not
re-observation of the same fact).

## POL-05 — no_mission_warranted is valid

A real signal may receive disposition `no_mission_warranted` with no
MissionContract opened. The system must not require a candidate to become a
mission. A future Director that always produces work is unsafe.

## POL-06 — Existing work/PR overlap must be respected

Known overlapping open PRs, issues, or in-flight missions are checked before
opening work; overlap references are recorded on the candidate
(`overlap_refs`). Duplicate work is not opened.

## POL-07 — No unnecessary architecture expansion

Missions must not introduce new runtime frameworks, schedulers, databases,
or orchestration infrastructure unless the original source explicitly
requires it.

## POL-08 — Production must not be changed merely to make a test green

A repair may not weaken legitimate production semantics to satisfy a test.
Scout/Review determine intent from repository evidence.

## POL-09 — Scout/Review may contradict the initial hypothesis

The Scout candidate may refute the mission's preliminary hypothesis, and the
Reviewer may reject a repair. A mission's expected outcome is not a verdict
in advance.

## POL-10 — Mission framing itself may be rejected

A MissionContract whose framing/scope is invalid, unsafe, duplicate,
unsupported, or evidence-inadequate is representable as `mission_rejected` —
distinct from "Scout found no defect". A technically valid workflow must be
able to say "this mission should not have been executed as framed". (v0.1
defines and validates the representation; runtime enforcement is a later
slice.)

## POL-11 — Role separation remains mandatory

Director ≠ Scout ≠ Operator ≠ Reviewer. No role performs another role's
work. The future Director may CREATE a bounded Recon Mission through the
existing Operator path; it does not perform broad source-code recon itself.

## POL-12 — Normal Gate-controlled integration only

Target and run-record integration proceed through the existing Review Gate
(`federation-hq/review`, App-owned) and NORMAL merges only — no admin
bypass, no force.

## POL-13 — No human-as-role-dispatcher when automated execution is available

When a compatible execution adapter is available (Automated Role Execution
v0.1), the Operator dispatches isolated role workers itself; the human is
not required to launch roles. Manual fallback remains supported.

## POL-14 — No autonomous infinite work generation

Mission formulation must not loop autonomously generating new work from its
own output. Each cycle requires a new explicit signal or human request.

## POL-15 — No LLM self-scoring as policy evidence

Candidates and contracts record explicit facts and enums. Numeric
self-assessment (risk_score, confidence, importance) is not policy evidence
and is not part of the v0.1 contracts.
