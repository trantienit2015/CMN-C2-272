# PB-7: HITL interrupt propagation (templates with hitl.enabled: true).
#
# Verifies interrupt() fired inside ApprovalExecute.execute() raises GraphInterrupt,
# the signal propagates through BaseNode.__call__() and is NOT caught by the
# application error boundary (status is NOT set to error). Here we drive the
# inner subgraph directly (compiled with a checkpointer) and assert the invoke
# suspends with AWAITING_HUMAN rather than error.

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from langgraph.checkpoint.memory import InMemorySaver

from src.graph.domain_workflow_graph import ChangeApprovalWorkflowGraph
from src.services.change_request_service import ChangeRequestClient


def _inner():
    client = ChangeRequestClient(groups={"CHG0001": {"approver1"}}, risks={"CHG0001": 0.2})
    sg = ChangeApprovalWorkflowGraph(
        config={"change_request_client": client, "memory_enabled": True, "hitl": {"enabled": True}}
    )
    sg.compile(checkpointer=InMemorySaver())
    return sg


class TestHitlInterruptPropagation:
    def test_interrupt_suspends_not_error(self):
        sg = _inner()
        payload = json.dumps({"change_request_id": "CHG0001", "requesting_user_id": "approver1", "urgency": "High"})
        ctx = InvocationContext(session_id="pb7", caller_trust_level=TrustLevel.INTERNAL, caller_id="approver1")
        # Seed the sanitized fields the inner nodes expect (inner re-parses user_input).
        result = sg.invoke(payload, ctx=ctx)
        # An authorized, non-auto-reject change reaches ApprovalExecute → interrupt().
        # The graph must suspend (AWAITING_HUMAN), not resolve to error.
        status = result.get("status")
        assert status in (
            AgentStatus.AWAITING_HUMAN,
            AgentStatus.AWAITING_HUMAN.value,
        ), f"expected AWAITING_HUMAN, got {status}: {result.get('error_log')}"
        assert status not in (AgentStatus.ERROR, AgentStatus.ERROR.value)
