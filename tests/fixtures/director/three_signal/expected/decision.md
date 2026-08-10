# Expected Director decision (NON-CANONICAL fixture, Issue #33)

- Signal A (sig-director-fixture-a): ELIGIBLE → exactly one selected
  MissionCandidate + one MissionContract (mission-director-fixture-a).
- Signal B (sig-director-fixture-b): prior terminal disposition wont_fix,
  no new evidence → MUST NOT reopen, MUST NOT select; disposition wont_fix,
  no MissionContract.
- Signal C (sig-director-fixture-c): POL-02 unbounded request → no
  executable MissionContract; disposition no_mission_warranted.

Zero numeric ranking; zero deep code recon.
