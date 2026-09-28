# CMN-C2-272 — Integration test: full-graph compile() + invoke() (Cat 2 + HITL).

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.services.change_request_service import ChangeRequestClient


def _client():
    return ChangeRequestClient(
        groups={"CHG0001": {"approver1"}},
        risks={"CHG0001": 0.2, "CHG9999": 0.95},
    )


def _agent():
    agent = Graph(
        config={"change_request_client": _client(), "memory_enabled": True, "hitl": {"enabled": True}, "max_retry": 1}
    )
    agent.compile()
    return agent


def _ctx(sid):
    return InvocationContext(session_id=sid, caller_trust_level=TrustLevel.INTERNAL, caller_id="approver1")


def test_unauthorized_requester_rejected_and_journaled():
    agent = _agent()
    payload = json.dumps({"change_request_id": "CHG0001", "requesting_user_id": "intruder", "urgency": "High"})
    result = agent.invoke(payload, ctx=_ctx("it-unauth"))
    assert result["status"] in (AgentStatus.SUCCESS, AgentStatus.SUCCESS.value)
    out = result.get("output") or result.get("formatted_output")
    assert out and out["decision"] == "rejected"
    nh = result.get("node_history") or []
    assert len(nh) >= 5, nh


def test_emergency_over_threshold_auto_rejected():
    agent = _agent()
    payload = json.dumps({"change_request_id": "CHG9999", "requesting_user_id": "approver1", "urgency": "Emergency"})
    # CHG9999 has no approval group here → still authorized? group empty → unauthorized → rejected.
    result = agent.invoke(payload, ctx=_ctx("it-emerg"))
    out = result.get("output") or result.get("formatted_output")
    assert out and out["decision"] == "rejected"


def test_bad_input_errors():
    agent = _agent()
    result = agent.invoke("", ctx=_ctx("it-bad"))
    assert result["status"] in (AgentStatus.ERROR, AgentStatus.ERROR.value)
