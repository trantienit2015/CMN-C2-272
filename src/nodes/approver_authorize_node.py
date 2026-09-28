"""AgentCore Platform v1.0 — CMN-C2-272 ApproverAuthorize (inner node)."""

# Step 2 · ApproverAuthorize — first inner node: parses user_input (JSON) and
# checks whether the requester is in the designated approval group via the
# ServiceNow Groups API. Fail-closed HARD STOP (ERROR) if unauthorized.

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.change_request_service import ChangeRequestClient, is_authorized


class ApproverAuthorizeNode(FunctionNode):
    """Verify the requester belongs to the designated approval group (fail-closed)."""

    # S-1: matches config/agent.yaml's required_trust_level (see InputValidateNode) —
    # this inner node must not impose a stricter gate than the entry point admits.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, client: ChangeRequestClient | None = None):
        self._client = client or ChangeRequestClient()

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        cr_id = state.get("change_request_id")
        user_id = state.get("requesting_user_id")
        if not cr_id or not user_id:
            raw = state.get("user_input", "")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(payload, dict):
                    cr_id = cr_id or payload.get("change_request_id")
                    user_id = user_id or payload.get("requesting_user_id")
            except (ValueError, TypeError):
                pass

        if not cr_id or not user_id:
            return {"status": AgentStatus.ERROR.value, "error_log": ["ApproverAuthorize: missing cr_id/user_id"]}

        group = self._client.approval_group(cr_id)
        authorized = is_authorized(user_id, group)

        emit_trace_event(
            "approver_authorization_checked",
            {"correlation_id": state.get("correlation_id"), "cr_id": cr_id, "authorized": authorized},
            state,
        )

        # Fail-closed: an unauthorized requester yields a rejected verdict (a
        # business decision, journaled downstream), NOT a system error — so the
        # pipeline continues to the NISC journal. ApprovalExecute skips the HITL
        # write when approver_authorized is False.
        return {
            "change_request_id": cr_id,
            "requesting_user_id": user_id,
            "approver_authorized": authorized,
            "status": AgentStatus.SUCCESS.value,
        }
