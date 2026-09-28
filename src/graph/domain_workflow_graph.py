"""AgentCore Platform v1.0 — CMN-C2-272 inner domain workflow graph."""

# Inner BaseGraph for the Cat 2 change-request approval pipeline (with HITL in
# ApprovalExecute). Linear topology:
#   START → approver_authorize → change_risk_assess → approval_execute(HITL) → END
# Downstream nodes pass through on incoming ERROR (fail-closed authorization is
# preserved to the end).

from typing import Any

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from langgraph.graph import END, START

from src.nodes.approval_execute_node import ApprovalExecuteNode
from src.nodes.approver_authorize_node import ApproverAuthorizeNode
from src.nodes.change_risk_assess_node import ChangeRiskAssessNode
from src.schemas.state import State


class ChangeApprovalWorkflowGraph(BaseGraph):
    """Inner graph: authorize → risk assess → HITL approval write."""

    @property
    def name(self) -> str:
        return "change_approval_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        client = self.config.get("change_request_client") if hasattr(self, "config") else None
        self._nodes["approver_authorize"] = ApproverAuthorizeNode(client=client)
        self._nodes["change_risk_assess"] = ChangeRiskAssessNode(client=client)
        self._nodes["approval_execute"] = ApprovalExecuteNode(client=client)

    def add_edges(self) -> None:
        self._sg.add_edge(START, "approver_authorize")
        self._sg.add_edge("approver_authorize", "change_risk_assess")
        self._sg.add_edge("change_risk_assess", "approval_execute")
        self._sg.add_edge("approval_execute", END)

    def route(self, state: dict[str, Any]) -> str:
        return END if state.get("status") == AgentStatus.ERROR.value else "approval_execute"

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "approver_authorized": state.get("approver_authorized"),
            "risk_score": state.get("risk_score"),
            "risk_verdict": state.get("risk_verdict"),
            "risk_rationale": state.get("risk_rationale"),
            "approval_decision": state.get("approval_decision"),
            "approval_reason": state.get("approval_reason"),
            "patch_result": state.get("patch_result"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
