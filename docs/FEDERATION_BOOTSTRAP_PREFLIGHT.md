Director Preflight — Federation Bootstrap Program Ingestion

> **Historical record.** The Step −1 preflight instruction below was executed
> before the bootstrap artifacts were ingested. It is recorded verbatim and is
> **not live direction**. Status: Step −1 executed and **completed**;
> Artifacts A and B ingested at `docs/FEDERATION_BOOTSTRAP_PROGRAM.md` and
> `docs/FEDERATION_BOOTSTRAP_DIRECTOR_INSTRUCTION.md`; PROGRAM 0 (S1) completed
> 2026-08-16 (`runs/run-20260813-steward-federation-nadi-source-binding` and
> the 0b/0c/0d run records, `mission/ledger.yaml` sig-20260813). This file is
> historical; for live direction follow
> `docs/FEDERATION_BOOTSTRAP_DIRECTOR_INSTRUCTION.md` and the execution record
> in `docs/FEDERATION_BOOTSTRAP_PROGRAM.md` §14.

---

You are the Mission Director operating inside kimeisele/federation-hq.

This is PRE-PROGRAM STEP −1.

Do not begin the Federation Bootstrap Program yet.
Do not formulate PROGRAM 0.
Do not create implementation missions.
Do not modify any target repository.

I have two finalized planning artifacts that must first become durable, repository-native Federation HQ program context:

1. Federation Bootstrap Program — Architecture & Program Brief
    (Artifact A)
2. Mission Director — Federation Bootstrap Program Instruction
    (Artifact B)

Your task is only to determine how these artifacts should be ingested into the existing Federation HQ repository.

Step 1 — Inspect existing repository conventions

Inspect the current repository structure and relevant documentation, especially locations used for:

* architecture/program documents;
* mission/program planning;
* durable orchestration state;
* policies;
* run artifacts;
* Director instructions;
* ledgers.

Do not invent a new directory or convention if an existing one fits.

Step 2 — Recommend exact placement

Tell me the exact repository paths where Artifact A and Artifact B should live.

For each path briefly explain why it matches Federation HQ’s existing architecture and conventions.

Also determine whether:

* these should be two separate files;
* one should reference the other;
* the existing ledger or another existing durable artifact should reference this program;
* any lightweight program identifier is necessary.

Do not introduce a new schema or program_id merely for elegance. Use existing structures if sufficient.

Step 3 — Give me the ingestion instruction

After deciding the locations, tell me exactly what you need from me.

I will provide the complete contents of Artifact A and Artifact B.

Do not paraphrase or reconstruct them from prior context.

Once I provide them:

1. write them verbatim to the approved repository paths;
2. make only formatting changes required by existing repository conventions;
3. verify the resulting files against what I supplied;
4. update only the minimal existing index/ledger/reference necessary to make the program discoverable;
5. commit the ingestion as a documentation/planning-only commit;
6. report the commit SHA and exact paths.

Do not begin PROGRAM 0 in the same commit.

Step 4 — Bootstrap readiness check

After ingestion, perform a short preflight against Artifact B.

Confirm that the Mission Director can locate and follow:

* Artifact A as canonical program context;
* Artifact B as the bootstrap execution instruction;
* existing Federation HQ policies;
* existing role boundaries;
* existing MissionContract / Operator / Scout / Builder / Reviewer path;
* the program ledger or equivalent durable tracking mechanism.

If anything prevents the program from starting safely, report it as a BLOCKER.

Do not fix it unless the fix is purely documentary and required for ingestion.

Stop point

When Step −1 is complete, STOP.

Return:

* Artifact A path
* Artifact B path
* any index/ledger/reference updated
* ingestion commit SHA
* readiness: READY or BLOCKED
* blockers, if any
* the exact next action that would initiate Artifact B

Do not initiate Artifact B yourself yet.

The purpose of Step −1 is to make the Federation Bootstrap Program durable and discoverable inside Federation HQ before orchestration begins.