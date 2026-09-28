"""AgentCore Platform v1.0 — CMN-C2-272 InputValidate (pre_process slot)."""

# Step 1 · InputValidate — S-1 trust gate (declared here) + deterministic input
# scan: change_request_id format + urgency enum allowlist. The framework
# @final _security_gate_input() runs the credential/PII scan; this node adds the
# domain format/enum validation and serializes validated_input (JSON) for the
# inner GraphNode.

import json
import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from shared.services.events import emitter
from shared.services.events.types import EventType
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.change_request_service import VALID_URGENCY

_CR_ID_RE = re.compile(r"^(CHG|CR)[0-9A-Za-z_-]{3,}$")


class InputValidateNode(FunctionNode):
    """Validate change_request_id format + urgency enum; sanitize input."""

    # S-1: entry-point gate. Matches config/agent.yaml's required_trust_level and the
    # entry-point auth boundary in src/api/server.py, which promotes ANONYMOUS -> VERIFIED_EXTERNAL
    # only on a valid INVOKE_AUTH_TOKEN — this node must accept that same level, not a stricter one.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Progress (non-terminal) event for the caller. Outside the Marketplace runtime
        # this resolves to a no-op emitter, so it is safe on every entry path.
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Checking the request...",
            metadata={"step": "pre_process"},
        )
        raw = state.get("user_input", "")
        payload = raw
        if isinstance(raw, str):
            if not raw.strip():
                return {"status": AgentStatus.ERROR.value, "error_log": ["InputValidate: empty input"]}
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                return {"status": AgentStatus.ERROR.value, "error_log": ["InputValidate: input is not valid JSON"]}

        if not isinstance(payload, dict):
            return {"status": AgentStatus.ERROR.value, "error_log": ["InputValidate: input must be an object"]}

        cr_id = str(payload.get("change_request_id", "")).strip()
        user_id = str(payload.get("requesting_user_id", "")).strip()
        urgency = str(payload.get("urgency", "")).strip()

        if not _CR_ID_RE.match(cr_id):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputValidate: invalid change_request_id format"],
            }
        if urgency not in VALID_URGENCY:
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"InputValidate: urgency must be one of {VALID_URGENCY}"],
            }
        if not user_id:
            return {"status": AgentStatus.ERROR.value, "error_log": ["InputValidate: requesting_user_id required"]}

        emit_trace_event(
            "change_request_validated",
            {"correlation_id": state.get("correlation_id"), "cr_id": cr_id, "urgency": urgency},
            state,
        )
        return {
            "change_request_id": cr_id,
            "requesting_user_id": user_id,
            "urgency": urgency,
            "validated_input": json.dumps(
                {"change_request_id": cr_id, "requesting_user_id": user_id, "urgency": urgency},
                ensure_ascii=False,
            ),
            "status": AgentStatus.SUCCESS.value,
        }
