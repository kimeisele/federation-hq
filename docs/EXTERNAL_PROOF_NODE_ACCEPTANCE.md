# Gate 6 — External Proof Node Acceptance

> **Status:** PASS  
> **Template:** `kimeisele/agent-template` @ `1e3d289aff38ff4b9edefb7ddaba2631ad5280a1` (post PR #22)  
> **Final Candidate:** `kimeisele/agent-template-acceptance-node-05`  
> **Node main SHA:** `63b70c19d4786c5e83644c8d8d48e163f2faab9e`

## Offline Proofs — PASS

| Check | Result |
|-------|--------|
| Core `.[dev]` | 267 passed, 13 skipped, Exit 0 |
| Federation `.[dev,federation]` | 280 passed, Exit 0 |
| Display name | "Final Acceptance Node 05" (5/5 artifacts) |
| Machine identity | `repo_id` = `agent-template-acceptance-node-05` |
| Governance | `agent-federation-baseline-v1` ACTIVE, CONFORMANT |
| Direct push blocked | "Changes must be made through a pull request." |
| `.venv*` gitignore | Present |
| Inbox/outbox | Empty |
| `.node_keys.json` | Not present |
| Fresh reclone | `63b70c19d4786c5e83644c8d8d48e163f2faab9e` — 267 core, 280 federation, ruff clean |

## Live Proofs — PASS

### 1. Secretless Workflow

| Field | Value |
|-------|-------|
| **Run ID** | `29848638989` |
| **Conclusion** | **SUCCESS** |
| **Guard** | `REMOTE_DISABLED_MISSING_PAT` |

### 2. Invalid-Key

| Field | Value |
|-------|-------|
| **Run ID** | `29848940075` |
| **Conclusion** | **FAILURE** (expected) |
| **Error** | `No usable node identity: NODE_PRIVATE_KEY is unset or unparseable` |

### 3. Valid Remote Heartbeat E2E

| Field | Value |
|-------|-------|
| **Run ID** | `29860023839` |
| **Node SHA** | `63b70c19d4786c5e83644c8d8d48e163f2faab9e` |
| **Conclusion** | **SUCCESS** |
| **Source** | `ag_ed8a1079acc8c9e6` |
| **Hub agent ID** | `agent-template-acceptance-node-05` |
| **Postcondition** | `8 heartbeat ID(s) confirmed in 8 hub file(s)` |

### Previous Failed Runs — INVALIDATED

Runs `29849222134` and `29854586384` failed because the old postcondition searched hub files using cryptographic `message.source` instead of transport `hub_agent_id`. Template PR #22 fixed this identity mismatch. They are not PAT or persistence evidence.

### Restricted-PAT — NOT EXECUTED (optional hardening)

## Workflow Inventory — PASS

| Workflow | Latest Run | Conclusion |
|----------|-----------|------------|
| `sync-agent-card.yml` | `29848524256` | SUCCESS |
| `sync-federation-descriptor.yml` | `29848524180` | SUCCESS |
| `publish-authority-feed.yml` | `29848524726` | SUCCESS |
| `heartbeat.yml` | `29860023839` | **SUCCESS** |
| `federation-discovery.yml` | Scheduled weekly | INVENTORIED |

## Acceptance Matrix — All PASS

| AT-REC | Finding | Gate | Status |
|--------|---------|------|--------|
| AT-REC-001 through AT-REC-017 | All 17 findings | 1–5 | **PASS** |

## Template Fixes (Gate 6)

| PR | Description |
|----|-------------|
| #17 | Display name from committed capabilities.json |
| #18 | Core profile NADI test skip |
| #19 | Defensive `_check_topic()` |
| #20 | `.venv*` gitignore |
| #21 | Ruff CI fixes |
| #22 | Postcondition hub_agent_id/message_source separation |

## Superseded

| Node | Disposition |
|------|------------|
| 01–04 | Diagnostic/development, various findings resolved by template PRs |
