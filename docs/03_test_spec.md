# CMN-C2-272 — Test Specification

## Test Strategy
- Unit (per node) + Integration (full graph) + Proof-of-Boundary (incl. PB-7 HITL).
- ServiceNow client in-memory default (no network/credential); interrupt monkeypatched in unit/PB-6.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations (S-5 enforcement is done in CI) | |
| TC-04 | InvocationContext via configurable only | Direct access raises error | |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused | |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial when domain checks needed | Domain-specific input checks execute correctly (e.g. PII scan on additional fields, consent validation, business rules) | Hook body non-trivial |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial when domain checks needed | Domain-specific output checks execute correctly (e.g. nested credential scan, PII re-check, content filtering, preservation verification) | Hook body non-trivial |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | Domain event emitted on every invocation path | ≥1 per node |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | |
| PB-3 | Level 2 → External service | Real external service connection | Data retrieved | |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | |
| PB-6 | Invoke execution order | `__call__()`: S-1 → S-4 node_start → S-2 → `execute()` → S-3 → S-4 node_complete | Order verified (`test_pb_invoke_order.py`; interrupt monkeypatched — adaptation §A#1) | |
| PB-7 | HITL interrupt propagation | `interrupt()` in ApprovalExecute suspends the inner graph | status = AWAITING_HUMAN, NOT error (`test_pb_hitl_interrupt.py`) | |

## Business Logic Tests (`tests/unit/test_nodes.py`, `tests/integration/test_graph.py`)

| TC-ID | Test | Input | Expected Result |
|-------|------|-------|----------------|
| BL-01 | InputValidate success/format/enum/empty | valid / bad cr_id / bad urgency / "" | SUCCESS / ERROR |
| BL-02 | ApproverAuthorize authorized | requester in group | approver_authorized=True |
| BL-03 | ApproverAuthorize unauthorized | requester not in group | approver_authorized=False (business reject, not error) |
| BL-04 | ChangeRiskAssess verdicts | low score / Emergency+high | ok / auto_reject |
| BL-04b | ChangeRiskAssess LLM rationale (Step 8e, test-double only, no real network call) | well-formed / markdown-fenced / malformed / raising / no-llm+no-secret / no-input | rationale set / rationale set / omitted / omitted / omitted / LLM never called — verdict always unaffected |
| BL-05 | ApprovalExecute unauthorized | approver_authorized=False | rejected, no HITL |
| BL-06 | ApprovalExecute auto-reject | risk_verdict=auto_reject | rejected, no HITL |
| BL-07 | ApprovalExecute HITL approve | resume feedback=approved | approved, PATCH state=Approved |
| BL-08 | NISCJournal | decision + reason | journal record + verdict |
| BL-09 | Full graph unauthorized | JSON payload, intruder | rejected + journaled, node_history ≥5 |
| BL-10 | Full graph bad input | "" | ERROR |
| PB-7 | Inner HITL suspend | authorized non-auto-reject change | AWAITING_HUMAN (not error) |

## HITL Compliance (criterion #12)
- D6 `interrupt()` only; `memory_enabled: true`; `max_hitl: 8`; PB-7 verifies suspend-not-error.

## Test Execution Summary
- Local: 40 passed (incl. PB-7 + integration + Step 8e LLM-rationale coverage); PB-6 is a CI gate of record (passes under CI wheel).
- CI `run-tests`: `pytest tests/` + `pytest tests/proof_of_boundary/`.

## 1. Deployment path tests

### TC-DEP-01 Marketplace entry point identity — `tests/unit/test_cli_entry_point.py`

| Case | Expected |
|---|---|
| Identity is concrete | `agent_name` / `namespace` are non-empty and not the runner default |
| Identity matches the HTTP entry point | the values equal what `src/api/server.py` passes to `secrets_factory`; where that entry point provisions no secrets, they equal the manifest `namespace` and the lower-cased template id |
| The runner call uses the constants | `run_agent_marketplace` is called with the module constants, not inline literals |

### TC-DEP-02 Standalone entry point boundary — `tests/proof_of_boundary/test_server_llm_injection.py`

| Case | Expected |
|---|---|
| Boots with no credential provisioned | the module imports and the app/agent objects are constructed |
| External bearer never reaches the internal level | resolves to the verified-external level |
| Staging runner credential reaches the internal level | resolves to the internal level |
| Wrong or missing bearer while auth is enabled | rejected with 401 |
