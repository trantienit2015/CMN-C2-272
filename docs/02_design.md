# CMN-C2-272 — Template Design Specification

ServiceNow Change Request Approve Agent (Cat 2 + HITL). 

## Position in AgentCore Architecture

- **Agent Class**: `Graph` (`src/graph/graph.py`) — `class Graph(AgentBaseGraph)`.
- **L1 Base**: **AgentBaseGraph** (L1 direct). ToolCallingAgent is a pattern reference only.
- **Category**: Cat 2 (outer AgentBaseGraph + `GraphNode` in `main` + inner `BaseGraph`).
- **HITL**: `hitl.enabled: true`, `memory_enabled: true` (D6 `interrupt()` in ApprovalExecute).

## Architecture Overview

### Cat 2 layering (with HITL)

```
OUTER (AgentBaseGraph — src/graph/graph.py):
  initialize → pre_process(InputValidate) → main(ChangeApprovalGraphNode) → post_process(NISCJournal) → finalize

INNER (BaseGraph — src/graph/domain_workflow_graph.py, compiled with a checkpointer):
  START → approver_authorize → change_risk_assess → approval_execute(D6 interrupt) → END
```

`ChangeApprovalGraphNode` (GraphNode) lives in `src/graph/graph.py` (not `src/nodes/`) so PB-6
does not probe it. It sets `propagate_hitl=True`, caches the checkpointer-compiled inner graph,
forwards `memory_enabled` to the inner, and re-raises `GraphBubbleUp` in `_handle_call_error`.

### Node Configuration

| Node | Slot | Responsibility | Trust |
|------|------|----------------|-------|
| InputValidateNode | pre_process | S-1 gate (INTERNAL) + cr_id format + urgency enum; serialize validated_input | INTERNAL |
| ChangeApprovalGraphNode | main | dispatch to inner; propagate HITL; merge | (GraphNode) |
| — ApproverAuthorizeNode | inner | approval-group membership (fail-closed → rejected verdict) | INTERNAL |
| — ChangeRiskAssessNode | inner | deterministic risk verdict (auto_reject/needs_review/ok) + optional LLM rationale (Step 8e) | INTERNAL |
| — ApprovalExecuteNode | inner | **HITL D6 interrupt** → PATCH change_request | VERIFIED_EXTERNAL |
| NISCJournalNode | post_process | **S-4** NISC-2026 audit journal + verdict | INTERNAL |

### HITL design (D6)

- `ApprovalExecute.execute()` calls `interrupt({"draft": ..., "reason": ...})` before the write.
- Auto-reject (Emergency > threshold) and unauthorized-requester paths skip the interrupt (no human loop).
- `hitl_draft` guards against re-execution on resume; on resume `hitl_feedback` carries the decision.
- Inner graph is compiled with `InMemorySaver`; `memory_enabled` forwarded so thread config exists.
- Permitted HITL state fields only (schema 1.4); templates write `hitl_draft` only.

### State Definition

| Field | Type | Purpose |
|-------|------|---------|
| change_request_id / requesting_user_id / urgency | NotRequired[str] | validated input |
| approver_authorized | NotRequired[bool] | group membership result |
| risk_score / risk_verdict | NotRequired[float/str] | deterministic risk assessment |
| risk_rationale | NotRequired[str] | optional LLM-generated explanation; absent on any LLM failure |
| approval_decision / approval_reason | NotRequired[str] | verdict |
| patch_result | NotRequired[dict] | ServiceNow PATCH result |
| nisc_journal / verdict | NotRequired[dict] | audit record + output |

## Security (5-layer)

- **S-1** `required_trust_level` explicit on every FunctionNode; write node = VERIFIED_EXTERNAL, others INTERNAL. Agent default VERIFIED_EXTERNAL in `agent.yaml`.
- **S-2** framework `@final` PII scan; InputValidate adds deterministic format/enum checks.
- **S-3** ServiceNow credential via injected client / `ctx.secrets.require()`; declared in `requires.secrets` (`SERVICENOW_API_TOKEN`). Azure OpenAI credential (`AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_DEPLOYMENT`, Step 8e) resolved the same way, built fresh per-invocation inside `ChangeRiskAssessNode.execute()` — never cached on the node instance (node instances are reused across callers via the registry's LRU cache). Never `os.environ`, never in state.
- **S-4** `emit_trace_event()` ≥1× per node; NISCJournal emits the audit record; GraphNode emits via hooks.
- **S-5** no credentials in source/State.

### Composition Pattern
- **Pattern**: GraphNode + inner BaseGraph (checkpointer-backed). **Error propagation**: `handle` (fail-closed rejection surfaces as a rejected verdict, still journaled); HITL `GraphBubbleUp` re-raised.

## Import Isolation
- No Level 0 / mediator / sibling imports; only `framework.*` / `shared.*` / `src.*` / `langgraph.*`.

## HITL Compliance (criterion #12)
- D6 `interrupt()` only; `memory_enabled: true` present; `max_hitl: 8`; PB-7 test verifies the interrupt suspends (AWAITING_HUMAN), not error.

## Design Decision Record

| Decision | Chosen | Rationale |
|----------|--------|-----------|
| L1 base type | AgentBaseGraph | Cat 2 gated multi-step approval workflow |
| Composition | GraphNode + inner BaseGraph, checkpointer | HITL interrupt needs pause/resume |
| Unauthorized handling | rejected verdict (not system error) | must still write NISC journal |
| error_strategy | handle | surface fail-closed rejection gracefully; re-raise HITL bubble |

## 1. Deployment entry points

The agent is reachable through two entry points, and both resolve their runtime inputs the same way.

| Entry point | File | Used by |
|---|---|---|
| Standalone HTTP | `src/api/server.py` | staging rehearsal, direct invocation |
| Marketplace one-shot Pod | `cli.py` | the platform runner (container `CMD`) |

Both read runtime parameters from `config/config.yaml` — the manifest carries discovery metadata
only — and both scope secrets to the same location (`namespace=cmn`, `agent_name=cmn-c2-272`), so a
secret provisioned for one path resolves identically on the other.

**Known limitation — HITL is structurally incompatible with the Marketplace one-shot Pod.** The
Marketplace adapter (AgentCore 1.0.3, `shared/bootstrap/marketplace_app.py`) treats
`AWAITING_HUMAN` as a failure: a one-shot Pod invocation has no resume channel, so the D6
`interrupt()` in `ApprovalExecuteNode` (see HITL design above) turns the first real approval into
`status='error'` with no error detail forwarded, rather than pausing for a human decision. This is
not a bug in this template — it is a present-state platform contract gap (no durable cross-Pod
resume). Do not disable or bypass `hitl.enabled` to work around this — HITL is this
template's whole purpose (gated change approval). The standalone HTTP entry point
(`src/api/server.py`) is unaffected and remains the supported path for real approval flows; the
Marketplace one-shot Pod entry point is provisionally out of scope for any invocation that reaches
the interrupt, until the platform supports cross-Pod resume.

**No LLM client is constructed at either entry point (`src/api/server.py` / `cli.py`).** The
approval decision pipeline (authorize → risk-verdict → HITL write → journal) remains fully
deterministic. `ChangeRiskAssessNode` alone builds an `AzureOpenAIClient` per-invocation, inside its
own `execute()`, to generate an optional plain-English `risk_rationale` (Step 8e enhancement) — this
never affects `risk_verdict` and any failure (missing secret, API error, malformed response)
silently omits the field rather than raising or degrading the deterministic result.

**Progress events.** Each pipeline stage emits a non-terminal progress event at its start, so a
caller sees the run advancing. Outside the Marketplace runtime the emitter resolves to a no-op, so
the same code is safe on every path. Terminal events belong to the platform runner and are never
emitted by this agent.

**Caller authentication at the standalone entry point.** A caller that no upstream middleware
vouched for stays anonymous unless it presents a deployment-level credential: the external bearer
token grants the verified-external level, and a separate staging-only runner credential is the only
way to reach the internal level. Trust established upstream is never changed.
