"""AgentCore Platform v1.0 — CMN-C2-272 ChangeRiskAssess (inner node)."""

# Step 3 · ChangeRiskAssess — retrieve the change risk score and apply a
# deterministic threshold (unchanged verdict logic; no LLM in the decision
# path). Emergency change above threshold → auto-reject branch; the actual
# reject is finalized in ApprovalExecute. An LLM-generated plain-English
# rationale is layered on top as an enhancement only (Step 8e): any failure
# (missing secret, API error, malformed response) silently omits
# risk_rationale — the deterministic verdict is never affected.

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.azure_openai_client import AzureOpenAIClient
from shared.utils.audit_logger import emit_trace_event
from shared.utils.llm_json import extract_json_object

from src.services.change_request_service import ChangeRequestClient, assess_risk

_RATIONALE_SYSTEM_PROMPT = (
    "You write a one-paragraph, plain-English rationale for a ServiceNow change-request risk "
    "verdict, for an IT operations audience. Use only the facts given; do not invent details. "
    'Respond with a JSON object of the exact shape {"rationale": "..."} and nothing else.'
)


class ChangeRiskAssessNode(FunctionNode):
    """Deterministic risk verdict (auto_reject / needs_review / ok) + optional LLM rationale."""

    # S-1: matches config/agent.yaml's required_trust_level (see InputValidateNode) —
    # this inner node must not impose a stricter gate than the entry point admits.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, client: ChangeRequestClient | None = None, llm: Any | None = None):
        self._client = client or ChangeRequestClient()
        # Test-double seam only — register_nodes() never passes this in production.
        self._llm = llm

    def _generate_rationale(
        self, state: dict[str, Any], cr_id: str, urgency: str, score: float, verdict: str
    ) -> str | None:
        """Best-effort LLM rationale; any failure degrades to no rationale (never raises)."""
        try:
            llm = self._llm
            if llm is None:
                ctx = InvocationContext.from_state(state)
                llm = AzureOpenAIClient(
                    {
                        "api_key": ctx.secrets.require("AZURE_OPENAI_API_KEY"),
                        "azure_endpoint": ctx.secrets.require("AZURE_OPENAI_ENDPOINT"),
                        "azure_deployment": ctx.secrets.require("AZURE_OPENAI_DEPLOYMENT"),
                    }
                )
            response = llm.complete(
                [
                    {"role": "system", "content": _RATIONALE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "change_request_id": cr_id,
                                "urgency": urgency,
                                "risk_score": score,
                                "verdict": verdict,
                            }
                        ),
                    },
                ]
            )
            parsed = extract_json_object(response.get("content", ""))
            rationale = parsed.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                return None
            return rationale.strip()
        except Exception:
            return None

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("status") in (AgentStatus.ERROR, AgentStatus.ERROR.value):
            return {"status": AgentStatus.ERROR.value}

        cr_id = state.get("change_request_id")
        urgency = state.get("urgency", "")
        if not cr_id:
            return {"status": AgentStatus.ERROR.value, "error_log": ["ChangeRiskAssess: missing cr_id"]}

        score = self._client.risk_score(cr_id)
        verdict = assess_risk(urgency, score)

        emit_trace_event(
            "change_risk_assessed",
            {"correlation_id": state.get("correlation_id"), "cr_id": cr_id, "risk_score": score, "verdict": verdict},
            state,
        )
        result: dict[str, Any] = {
            "risk_score": score,
            "risk_verdict": verdict,
            "status": AgentStatus.SUCCESS.value,
        }
        rationale = self._generate_rationale(state, cr_id, urgency, score, verdict)
        if rationale is not None:
            result["risk_rationale"] = rationale
        return result
