"""AgentCore Platform v1.0 — CMN-C2-272 state schema."""

# ADR-005: flat TypedDict; no credentials/secrets/Pydantic. state-safety: agent-specific
# fields wrapped in NotRequired[...]. HITL fields (hitl_feedback, hitl_draft,
# hitl_status, ...) are inherited from AgentState — do NOT redeclare them.

from typing import Any, NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """State for the ServiceNow change-request approval agent."""

    # pre_process (InputValidate)
    change_request_id: NotRequired[str]
    requesting_user_id: NotRequired[str]
    urgency: NotRequired[str]

    # inner: ApproverAuthorize
    approver_authorized: NotRequired[bool]

    # inner: ChangeRiskAssess
    risk_score: NotRequired[float]
    risk_verdict: NotRequired[str]  # "auto_reject" | "needs_review" | "ok"
    risk_rationale: NotRequired[str]  # optional LLM-generated explanation; absent on any LLM failure

    # inner: ApprovalExecute (HITL D6)
    approval_decision: NotRequired[str]  # "approved" | "rejected"
    approval_reason: NotRequired[str]
    patch_result: NotRequired[dict[str, Any]]

    # post_process: NISCJournal
    nisc_journal: NotRequired[dict[str, Any]]
    verdict: NotRequired[dict[str, Any]]
