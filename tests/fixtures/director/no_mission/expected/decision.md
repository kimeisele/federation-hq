# Expected Director decision (NON-CANONICAL fixture, Issue #33)

- Signal D (sig-director-fixture-d): existing terminal Ledger disposition
  `completed` is authoritative and preserved — reported, NOT re-dispositioned,
  NO new MissionContract, NO prior_disposition_override invented.
- Signal E (sig-director-fixture-e): NEW unbounded request (POL-02) →
  schema-valid MissionCandidate disposition `no_mission_warranted` (see
  signal-e-candidate.yaml). No MissionContract.
- Signal F (sig-director-fixture-f): NEW signal duplicating D →
  schema-valid MissionCandidate disposition `duplicate`, `duplicate_of:
  sig-director-fixture-d` (see signal-f-candidate.yaml). No MissionContract.

MissionContract count: 0.
