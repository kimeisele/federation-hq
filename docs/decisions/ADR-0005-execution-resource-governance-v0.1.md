# ADR-0005 — Execution Resource Governance v0.1

- **Status:** Accepted (PR-local, Issue #39)
- **Date:** 2026-08-10
- **Prompt releases:** `repair@0.2.1`, `review@0.2.1`, `operator@0.3.1`
  (immutable successors; `repair@0.2.0`, `review@0.2.0`, `operator@0.3.0`
  bytes unchanged)
- **Helper:** `scripts/check_execution_pressure.py`

## Why

The FIRST LIVE HQ DIRECTOR PILOT (run #37, run-record PR #38) completed
successfully but exposed one concrete runtime defect: unbounded/repeated
heavy verification under host resource saturation. Canonical facts from the
run record:

```text
host: 4-core shared machine
peak load average: 630+
review load: ~300-337
head broad/full verification: ~1704s
baseline broad verification: ~879s
another baseline rerun: SIGTERM after ~687s
repair full-suite timeout: ~900s
repeated whole-suite retries materially extended wall-clock
human circuit breaker required
```

The run only completed after a human stopped further full-suite retries and
bounded verification produced an environmental-qualified `approved` Review.
The human must NOT be required as the resource circuit breaker; this behavior
must become normal autonomous role behavior.

## Decision

Replace the repetition pattern

```text
Repair full suite -> Reviewer full head suite -> Reviewer full baseline suite
-> timeout/saturation -> repeat -> hours of wall-clock
```

with

```text
bounded evidence plan -> at most one justified broad verification
-> targeted baseline differential checks -> environmental timeout
classification -> terminal verdict
```

Semantic Review is NOT weakened: the Reviewer still independently verifies
the exact remote head, candidate, MissionContract, repair scope, relevant
behavior, focused regression tests, and deterministic new failures. What
changes is that independent verification no longer means blindly rerunning
every expensive Repair command; the Reviewer chooses the minimum sufficient
verification set.

## Rules (v0.1)

1. **Heavy command** (documentation semantics, no new schema): repository-wide
   suite, very large integration suite, >~60s under normal conditions, or
   substantial subprocess/CPU load. Focused affected-component tests are
   normally not heavy.
2. **Repair budget** (`repair@0.2.1`): focused-first verification; broad/full
   suite only when repo governance requires it, the repair has broad
   cross-cutting effects, or bounded checks cannot establish safety. Max
   broad/full-suite executions per repair attempt: **1**.
3. **Reviewer differential strategy** (`review@0.2.1`): focused tests at head
   -> neighboring/component tests -> at most ONE broad head run -> baseline
   replay of ONLY the head failing test IDs plus concrete suspects (never the
   whole baseline suite). Reviewer broad/full-suite budget: default **1**,
   absolute max **2** per review attempt (second requires stating the concrete
   unresolved question first), never a third.
4. **Retry circuit breaker**: same heavy command + same SHA + same evidence
   question may not be automatically retried after timeout/SIGTERM/resource
   exhaustion (default 0 identical retries). Choose (A) bounded targeted
   verification, (B) alternative equivalent evidence, or (C) `blocked:
   environment_resource_exhaustion`. Retry only with concrete evidence the
   environment materially changed; record why. "No output yet" is not a
   reason.
5. **Host pressure preflight**: before any heavy command, run
   `scripts/check_execution_pressure.py` (OK | PRESSURED | UNKNOWN with
   cpu_count/load_1m/normalized_load). A heavy job SHOULD NOT start when
   PRESSURED. Threshold repository-configurable via
   `FHQ_HEAVY_LOAD_THRESHOLD` (normalized load = load_1m / cpu_count; default
   1.5 — a conservative v0.1 heuristic: a normalized load around 1.0 already
   means roughly one runnable/uninterruptible task per CPU on average, so a
   new heavy job should preserve headroom; load average is a pressure signal,
   not CPU utilization; the environment may override); transparent UNKNOWN
   where load average is unavailable; UNKNOWN does
   not block focused work. No daemon, scheduler, or telemetry database.
6. **No concurrent heavy work per run** (`operator@0.3.1`): max 1 concurrent
   heavy verification command within one run; timeout is not license for
   endless re-dispatch; persistent resource exhaustion terminalizes the run
   `blocked` with the concrete environmental cause via the existing blocker
   terminalization path.
7. **Environmental classification**: deterministic regression vs
   environment-induced timeout/resource exhaustion is distinguished only with
   evidence (same test times out at head AND baseline under comparable
   pressure; deterministic head/baseline failure IDs equal; targeted isolated
   rerun passes once pressure subsides; external termination rather than
   semantic failure). Residual uncertainty -> `blocked:
   environment_resource_exhaustion`; no indefinite retry to avoid it.
8. **No universal full-suite rule**: Federation HQ has no rule that every
   Repair and every Review must execute the entire target suite; verification
   depth is proportional to repair scope, affected components, available CI,
   and observed failures. Repository governance may require more.
9. **Existing CI**: exact-SHA repo CI, when trustworthy, may serve as the
   independent broad-suite evidence without duplicating it locally; CI is
   evidence, not semantic Reviewer authority.

## Boundaries

No new artifact schema; no scheduler/queue/lock/telemetry service; the
depth-2 harness spawn-cap finding from Pilot #37 is recorded as a separate
post-Pilot observation and is NOT addressed here. Director selection
semantics, Mission schemas, RunAssessment schema, Review Gate, Agent City
code, target protections, Scout semantics, and the GitHub canonical
control-plane model are unchanged.

## Acceptance evidence

Pilot #37 regression model: Repair focused passes + one broad head run -> 19
deterministic known failures + 22 Timeout failures; Reviewer focused tests
pass + one broad head verification + baseline replay of exact suspicious IDs
-> 19 pre-existing, timeout subset reproduces environmentally; expected NO
second whole baseline suite, NO third whole-suite run, verdict `approved`
with environmental qualification. Saturation that prevents classifying one
suspect -> `blocked environment_resource_exhaustion`. No infinite retries.

Mechanical tests cover the policy/helper boundaries only (not LLM judgment):
heavy retry circuit breaker; third Reviewer broad-suite attempt forbidden;
baseline differential by exact failing IDs instead of a full baseline suite;
one-run concurrent heavy lease bound; focused tests permitted; environmental
blocker valid; semantic Review independence intact.
