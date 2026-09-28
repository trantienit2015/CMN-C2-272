"""AgentCore Platform v1.0 — CMN-C2-272 outer graph (Cat 2 + HITL).

Cat 2 = AgentBaseGraph (outer backbone) + GraphNode in the `main` slot wrapping
an inner BaseGraph workflow. The inner ApprovalExecute node fires a D6
interrupt(); to surface it as AWAITING_HUMAN through the GraphNode boundary the
inner graph is compiled with a checkpointer, memory_enabled is forwarded, and
the GraphNode sets propagate_hitl=True and re-raises GraphBubbleUp.

Backbone:
  initialize → pre_process(InputValidate) → main(GraphNode) → post_process(NISCJournal) → finalize

The GraphNode wrapper lives in THIS file (not src/nodes/) so PB-6 does not probe it.
"""

from typing import Any, ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from shared.services.events import emitter
from shared.services.events.types import EventType
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp
from shared.utils.audit_logger import emit_trace_event

from src.nodes.input_validate_node import InputValidateNode
from src.nodes.nisc_journal_node import NISCJournalNode
from src.schemas.state import State


class ChangeApprovalGraphNode(GraphNode):
    """`main` slot — dispatches into the inner change-approval workflow (HITL)."""

    error_strategy: ClassVar[str] = "handle"
    propagate_hitl: ClassVar[bool] = True

    def __init__(self, change_request_client: Any | None = None) -> None:
        super().__init__()
        self._client = change_request_client
        self._subgraph: AgentBaseGraph | None = None  # cached so the checkpointer survives invoke→resume

    def get_subgraph(self) -> AgentBaseGraph:
        if self._subgraph is None:
            from src.graph.domain_workflow_graph import ChangeApprovalWorkflowGraph

            sg = ChangeApprovalWorkflowGraph(config=self._parent_config())
            sg.compile(checkpointer=InMemorySaver())
            self._subgraph = sg
        return self._subgraph

    def extract_input(self, state: AgentState) -> str:
        # S-4: runs inside GraphNode.execute() — audit dispatch into the subgraph.
        # Progress (non-terminal) event for the caller, emitted as the subgraph step
        # begins. Outside the Marketplace runtime this is a no-op emitter.
        emitter().emit_event(
            event_type=EventType.PROGRESS_UPDATE,
            message="Working on the request...",
            metadata={"step": "main"},
        )
        emit_trace_event(
            "approval_workflow_dispatched",
            {"correlation_id": state.get("correlation_id")},
            state,
        )
        value = state.get("validated_input", state.get("user_input", ""))
        return value if isinstance(value, str) else str(value)

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        # S-4: runs inside GraphNode.execute() — audit subgraph outcome.
        emit_trace_event(
            "approval_workflow_completed",
            {"correlation_id": state.get("correlation_id"), "decision": sub_result.get("approval_decision")},
            state,
        )
        return {
            "approver_authorized": sub_result.get("approver_authorized"),
            "risk_score": sub_result.get("risk_score"),
            "risk_verdict": sub_result.get("risk_verdict"),
            "risk_rationale": sub_result.get("risk_rationale"),
            "approval_decision": sub_result.get("approval_decision"),
            "approval_reason": sub_result.get("approval_reason"),
            "patch_result": sub_result.get("patch_result"),
            "status": sub_result.get("status"),
        }

    def _handle_call_error(self, subgraph: AgentBaseGraph, e: BaseException, state: dict[str, Any]) -> dict[str, Any]:
        # HITL interrupts arrive as GraphBubbleUp — must propagate, not be wrapped
        # into a SubgraphError (which would surface as status=error).
        if isinstance(e, GraphBubbleUp) or "interrupt" in type(e).__name__.lower():
            raise e
        result = super()._handle_call_error(subgraph, e, state)
        # The base contract returns a state update mapping; keep the declared
        # shape rather than passing an unexpected value through.
        return result if isinstance(result, dict) else {}

    def _parent_config(self) -> dict[str, Any]:
        return {
            "change_request_client": self._client,
            "memory_enabled": True,  # inner needs thread config for the checkpointer
            "hitl": {"enabled": True},
        }


class Graph(AgentBaseGraph):
    """Outer Cat 2 graph for CMN-C2-272 ServiceNow change-request approval."""

    @property
    def name(self) -> str:
        return "cmn-c2-272"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()
        client = self.config.get("change_request_client") if hasattr(self, "config") else None
        self._nodes["pre_process"] = InputValidateNode()
        self._nodes["main"] = ChangeApprovalGraphNode(change_request_client=client)
        self._nodes["post_process"] = NISCJournalNode()

    # add_edges() not overridden — backbone wiring belongs to the framework.
