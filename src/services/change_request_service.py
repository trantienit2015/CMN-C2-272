"""AgentCore Platform v1.0 — CMN-C2-272 change-request service.

Deterministic ServiceNow change-request helpers + a thin client abstraction:
  * approver-group authorization (deterministic membership check)
  * risk assessment (deterministic threshold; Emergency > threshold → auto-reject)
  * change_request PATCH (Table API) client with an in-memory default for tests.
No credentials embedded here; the real transport is injected.
"""

from __future__ import annotations

from typing import Any, Protocol

EMERGENCY_AUTO_REJECT_THRESHOLD = 0.75
VALID_URGENCY = ("Low", "Medium", "High", "Emergency")


def is_authorized(user_id: str, approval_group: set[str] | None) -> bool:
    """Deterministic membership check against the designated approval group."""
    if not user_id:
        return False
    group = approval_group or set()
    return user_id in group


def assess_risk(urgency: str, risk_score: float) -> str:
    """Return a deterministic risk verdict.

    Emergency change above the threshold → auto_reject (no LLM); high score →
    needs_review; otherwise ok.
    """
    if urgency == "Emergency" and risk_score >= EMERGENCY_AUTO_REJECT_THRESHOLD:
        return "auto_reject"
    if risk_score >= EMERGENCY_AUTO_REJECT_THRESHOLD:
        return "needs_review"
    return "ok"


class Transport(Protocol):
    """Surface an injected HTTP transport must expose."""

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class ChangeRequestClient:
    """Thin ServiceNow change_request Table API client.

    In production the transport carries a secret handle resolved via
    ctx.secrets.require(); the default in-memory transport records the PATCH so
    unit/integration tests run with no network and no credential.
    """

    def __init__(
        self,
        transport: "Transport | None" = None,
        groups: dict[str, Any] | None = None,
        risks: dict[str, Any] | None = None,
    ):
        self._transport = transport
        # {cr_id: set(user_ids)} — designated approval group membership.
        self._groups = groups or {}
        # {cr_id: risk_score} — pre-computed change risk scores.
        self._risks = risks or {}

    def approval_group(self, cr_id: str) -> set[str]:
        return set(self._groups.get(cr_id, set()))

    def risk_score(self, cr_id: str) -> float:
        return float(self._risks.get(cr_id, 0.0))

    def patch_change_request(self, cr_id: str, state_value: str) -> dict[str, Any]:
        """PATCH /api/now/table/change_request — set state (Approved/Rejected)."""
        if self._transport is not None:
            return self._transport.patch(f"/api/now/table/change_request/{cr_id}", {"state": state_value})
        return {"sys_id": cr_id, "state": state_value, "updated": True}
