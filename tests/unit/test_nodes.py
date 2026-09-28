# CMN-C2-272 — Unit tests: per-node success + error/edge paths.

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.approval_execute_node import ApprovalExecuteNode
from src.nodes.approver_authorize_node import ApproverAuthorizeNode
from src.nodes.change_risk_assess_node import ChangeRiskAssessNode
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.nisc_journal_node import NISCJournalNode
from src.services.change_request_service import ChangeRequestClient


def _client():
    return ChangeRequestClient(
        groups={"CHG0001": {"approver1"}},
        risks={"CHG0001": 0.2, "CHG9999": 0.9},
    )


class TestInputValidateNode:
    def test_success(self):
        payload = json.dumps({"change_request_id": "CHG0001", "requesting_user_id": "approver1", "urgency": "High"})
        r = InputValidateNode().execute({"user_input": payload, "correlation_id": "c"})
        assert r["status"] == AgentStatus.SUCCESS
        assert r["change_request_id"] == "CHG0001"
        assert "validated_input" in r

    def test_bad_cr_id(self):
        payload = json.dumps({"change_request_id": "XX1", "requesting_user_id": "u", "urgency": "High"})
        assert InputValidateNode().execute({"user_input": payload})["status"] == AgentStatus.ERROR

    def test_bad_urgency(self):
        payload = json.dumps({"change_request_id": "CHG0001", "requesting_user_id": "u", "urgency": "Whenever"})
        assert InputValidateNode().execute({"user_input": payload})["status"] == AgentStatus.ERROR

    def test_empty(self):
        assert InputValidateNode().execute({"user_input": ""})["status"] == AgentStatus.ERROR

    def test_trust_level_verified_external(self):
        # Matches config/agent.yaml's required_trust_level and the entry-point auth
        # boundary in src/api/server.py (promotes ANONYMOUS -> VERIFIED_EXTERNAL only).
        assert InputValidateNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL


class TestApproverAuthorizeNode:
    def test_authorized(self):
        r = ApproverAuthorizeNode(client=_client()).execute(
            {"change_request_id": "CHG0001", "requesting_user_id": "approver1", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["approver_authorized"] is True

    def test_unauthorized_is_business_reject_not_error(self):
        r = ApproverAuthorizeNode(client=_client()).execute(
            {"change_request_id": "CHG0001", "requesting_user_id": "intruder", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["approver_authorized"] is False

    def test_missing_ids_error(self):
        assert ApproverAuthorizeNode().execute({"user_input": "", "correlation_id": "c"})["status"] == AgentStatus.ERROR


class _FakeLLM:
    """Test double for AzureOpenAIClient — same complete(messages) -> dict shape."""

    def __init__(self, content=None, raises=None, calls=None):
        self._content = content
        self._raises = raises
        self._calls = calls if calls is not None else []

    def complete(self, messages):
        self._calls.append(messages)
        if self._raises is not None:
            raise self._raises
        return {"content": self._content}


class TestChangeRiskAssessNode:
    def test_ok_verdict(self):
        r = ChangeRiskAssessNode(client=_client()).execute(
            {"change_request_id": "CHG0001", "urgency": "High", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["risk_verdict"] == "ok"

    def test_emergency_auto_reject(self):
        r = ChangeRiskAssessNode(client=_client()).execute(
            {"change_request_id": "CHG9999", "urgency": "Emergency", "correlation_id": "c"}
        )
        assert r["risk_verdict"] == "auto_reject"

    def test_passthrough_on_error(self):
        assert ChangeRiskAssessNode().execute({"status": AgentStatus.ERROR})["status"] == AgentStatus.ERROR

    def test_no_llm_injected_no_secret_bound_degrades_silently(self):
        # The real production shape in any environment without a configured key:
        # InvocationContext.from_state(state) raises (missing session_id/thread_id/
        # trace_id here, or a MissingSecret once those are present) and is caught —
        # the deterministic verdict is unaffected, just no risk_rationale.
        r = ChangeRiskAssessNode(client=_client()).execute(
            {"change_request_id": "CHG0001", "urgency": "High", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["risk_verdict"] == "ok"
        assert "risk_rationale" not in r

    def test_llm_rationale_included_on_well_formed_json(self):
        llm = _FakeLLM(content='{"rationale": "Low score, standard change — approved risk profile."}')
        r = ChangeRiskAssessNode(client=_client(), llm=llm).execute(
            {"change_request_id": "CHG0001", "urgency": "High", "correlation_id": "c"}
        )
        assert r["risk_verdict"] == "ok"  # deterministic verdict unchanged
        assert r["risk_rationale"] == "Low score, standard change — approved risk profile."

    def test_llm_rationale_parses_markdown_fenced_response(self):
        llm = _FakeLLM(content='Here you go:\n```json\n{"rationale": "Elevated but acceptable."}\n```')
        r = ChangeRiskAssessNode(client=_client(), llm=llm).execute(
            {"change_request_id": "CHG0001", "urgency": "High", "correlation_id": "c"}
        )
        assert r["risk_rationale"] == "Elevated but acceptable."

    def test_llm_malformed_response_falls_back(self):
        llm = _FakeLLM(content='{"unexpected_field": 123}')
        r = ChangeRiskAssessNode(client=_client(), llm=llm).execute(
            {"change_request_id": "CHG0001", "urgency": "High", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["risk_verdict"] == "ok"
        assert "risk_rationale" not in r

    def test_llm_raising_falls_back(self):
        llm = _FakeLLM(raises=RuntimeError("Azure OpenAI unavailable"))
        r = ChangeRiskAssessNode(client=_client(), llm=llm).execute(
            {"change_request_id": "CHG0001", "urgency": "High", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["risk_verdict"] == "ok"
        assert "risk_rationale" not in r

    def test_no_input_never_calls_llm(self):
        calls: list = []
        llm = _FakeLLM(content='{"rationale": "should never be reached"}', calls=calls)
        r = ChangeRiskAssessNode(client=_client(), llm=llm).execute({"correlation_id": "c"})
        assert r["status"] == AgentStatus.ERROR
        assert calls == []


class TestApprovalExecuteNode:
    def test_unauthorized_rejects_without_hitl(self):
        r = ApprovalExecuteNode(client=_client()).execute(
            {"approver_authorized": False, "change_request_id": "CHG0001", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["approval_decision"] == "rejected"

    def test_auto_reject_no_hitl(self):
        r = ApprovalExecuteNode(client=_client()).execute(
            {
                "approver_authorized": True,
                "risk_verdict": "auto_reject",
                "change_request_id": "CHG9999",
                "correlation_id": "c",
            }
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["approval_decision"] == "rejected"

    def test_hitl_approve_on_resume(self, monkeypatch):
        import src.nodes.approval_execute_node as ae

        monkeypatch.setattr(ae, "interrupt", lambda payload: {"decision": "approved", "reason": "ok by ops"})
        r = ApprovalExecuteNode(client=_client()).execute(
            {"approver_authorized": True, "risk_verdict": "ok", "change_request_id": "CHG0001", "correlation_id": "c"}
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["approval_decision"] == "approved"
        assert r["patch_result"]["state"] == "Approved"

    def test_write_node_trust(self):
        assert ApprovalExecuteNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL


class TestNISCJournalNode:
    def test_journal_and_verdict(self):
        r = NISCJournalNode().execute(
            {
                "change_request_id": "CHG0001",
                "requesting_user_id": "approver1",
                "approval_decision": "approved",
                "approval_reason": "ok",
                "risk_score": 0.2,
                "correlation_id": "c",
            }
        )
        assert r["status"] == AgentStatus.SUCCESS
        assert r["nisc_journal"]["decision"] == "approved"
        assert r["verdict"]["change_request_id"] == "CHG0001"
