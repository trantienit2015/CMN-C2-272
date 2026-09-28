"""AgentCore Platform v1.0 — CMN-C2-272 NISCJournal (post_process slot, S-4)."""

# Step 5 · NISCJournal — S-4 audit: emit a NISC-2026 compliance journal record
# (approver identity + timestamp source + rationale) and assemble the final
# verdict. Does NOT re-emit framework node_start/node_complete/node_error.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from shared.services.events import emitter
from shared.services.events.types import EventType
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class NISCJournalNode(FunctionNode):
    """Write the NISC compliance journal record and assemble the verdict."""

    # S-1: matches config/agent.yaml's required_trust_level (see InputValidateNode) —
    # this inner node must not impose a stricter gate than the entry point admits.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Progress (non-terminal) event for the caller. Outside the Marketplace runtime
        # this resolves to a no-op emitter, so it is safe on every entry path.
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Preparing the result...",
            metadata={"step": "post_process"},
        )
        decision = state.get("approval_decision", "rejected")
        reason = state.get("approval_reason", "")
        journal = {
            "change_request_id": state.get("change_request_id"),
            "approver": state.get("requesting_user_id"),
            "decision": decision,
            "rationale": reason,
            "risk_score": state.get("risk_score"),
            "risk_rationale": state.get("risk_rationale"),
            "correlation_id": state.get("correlation_id"),
        }

        # S-4 domain event — the NISC-2026 audit record.
        emit_trace_event("nisc_journal_recorded", journal, state)

        verdict = {
            "change_request_id": state.get("change_request_id"),
            "decision": decision,
            "reason": reason,
        }
        return {
            "nisc_journal": journal,
            "verdict": verdict,
            "formatted_output": verdict,
            "status": AgentStatus.SUCCESS.value,
        }
