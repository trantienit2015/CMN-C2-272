"""AgentCore Platform v1.0 — CMN-C2-272 ApprovalExecute (inner node, HITL D6)."""

# Step 4 · ApprovalExecute — the state-changing write, gated by a D6 HITL
# interrupt(). Framework owns the pause/resume mechanism; this node owns the
# trigger. auto_reject verdicts short-circuit to a rejection with no interrupt.
# Otherwise the node interrupts for human confirmation before PATCHing the
# change_request to Approved/Rejected. hitl_draft guards against LLM/logic
# re-execution on resume.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from langgraph.types import interrupt
from shared.utils.audit_logger import emit_trace_event

from src.services.change_request_service import ChangeRequestClient


class ApprovalExecuteNode(FunctionNode):
    """HITL-gated approval write (D6 interrupt() → PATCH change_request)."""

    # S-1: privileged mutation → require a verified external caller (human/orchestrator).
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, client: ChangeRequestClient | None = None):
        self._client = client or ChangeRequestClient()

    def _write(self, state: dict[str, Any], decision: str, reason: str) -> dict[str, Any]:
        cr_id = str(state.get("change_request_id") or "")
        state_value = "Approved" if decision == "approved" else "Rejected"
        patch = self._client.patch_change_request(cr_id, state_value)
        emit_trace_event(
            "change_request_patched",
            {"correlation_id": state.get("correlation_id"), "cr_id": cr_id, "decision": decision},
            state,
        )
        return {
            "approval_decision": decision,
            "approval_reason": reason,
            "patch_result": patch,
            "status": AgentStatus.SUCCESS.value,
        }

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("status") in (AgentStatus.ERROR, AgentStatus.ERROR.value):
            return {"status": AgentStatus.ERROR.value}

        # Fail-closed: unauthorized approver → rejected verdict, no write, no HITL.
        if state.get("approver_authorized") is False:
            emit_trace_event(
                "change_request_rejected_unauthorized",
                {"correlation_id": state.get("correlation_id"), "cr_id": state.get("change_request_id")},
                state,
            )
            return {
                "approval_decision": "rejected",
                "approval_reason": "requester not in designated approval group (fail-closed)",
                "status": AgentStatus.SUCCESS.value,
            }

        # Deterministic auto-reject for Emergency-over-threshold — no human loop.
        if state.get("risk_verdict") == "auto_reject":
            emit_trace_event(
                "change_request_auto_rejected",
                {"correlation_id": state.get("correlation_id"), "cr_id": state.get("change_request_id")},
                state,
            )
            return self._write(state, "rejected", "auto-reject: Emergency change above risk threshold")

        # ADR-006 / HITL rule: hitl_allowed is a framework-managed,
        # fail-open field (default True) that only AuthMiddleware / a parent
        # GraphNode may set False. When HITL is unavailable in this execution
        # context, this write must not proceed unattended — fail closed to a
        # rejection rather than calling interrupt() (which would deadlock with
        # no human able to resume it).
        if not state.get("hitl_allowed", True):
            emit_trace_event(
                "change_request_rejected_hitl_unavailable",
                {"correlation_id": state.get("correlation_id"), "cr_id": state.get("change_request_id")},
                state,
            )
            return {
                "approval_decision": "rejected",
                "approval_reason": "HITL unavailable in this execution context (hitl_allowed=False) — "
                "fail-closed, no unattended approval write",
                "status": AgentStatus.SUCCESS.value,
            }

        # HITL D6: pause for human confirmation before the mutation.
        draft = state.get("hitl_draft")
        if draft is None:
            draft = {
                "change_request_id": state.get("change_request_id"),
                "risk_score": state.get("risk_score"),
                "urgency": state.get("urgency"),
                "proposed": "approve",
            }
        emit_trace_event(
            "approval_hitl_requested",
            {"correlation_id": state.get("correlation_id"), "cr_id": state.get("change_request_id")},
            state,
        )
        feedback = interrupt({"draft": draft, "reason": "approver confirmation required before write"})

        # On resume, feedback carries the human decision.
        decision = "approved"
        reason = "human-approved via HITL"
        if isinstance(feedback, dict):
            decision = "approved" if feedback.get("decision", "approved") == "approved" else "rejected"
            reason = feedback.get("reason", reason)
        elif isinstance(feedback, str):
            decision = "approved" if feedback.strip().lower() in ("approve", "approved", "yes") else "rejected"

        result = self._write(state, decision, reason)
        result["hitl_draft"] = draft  # idempotency guard on resume
        return result
