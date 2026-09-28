# tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py
#
# CONDITIONAL: Required only when config/config.yaml has hitl.enabled: true
# Templates that do not use HITL will see this file produce SKIPPED results.
#
# Node under test: ApprovalExecuteNode (src/nodes/approval_execute_node.py),
# the D6 HITL write gate for the change-request approval workflow.
#
# Two test cases (PB-7):
#
#   test_pb7_hitl_interrupt_propagates()
#       Verifies that interrupt() raises GraphInterrupt and the signal propagates
#       through BaseNode.__call__() to the LangGraph engine — NOT caught by the
#       application error boundary.
#
#   test_pb7_hitl_allowed_false_skips_interrupt()
#       Verifies that the hitl_allowed=False guard prevents interrupt() from
#       firing — no GraphInterrupt raised, no deadlock.
#
# What this covers:
#   PB-7 — interrupt() propagation across the node call boundary
#   HITL compliance — the D6 pattern and the mandatory hitl_allowed guard

from __future__ import annotations

import pathlib
import warnings

import pytest

# ---------------------------------------------------------------------------
# Conditional skip — only runs when config/config.yaml has hitl.enabled: true
# ---------------------------------------------------------------------------

_CONFIG_PATH = pathlib.Path(__file__).parents[2] / "config" / "config.yaml"


def _hitl_enabled() -> bool:
    """Return True when config/config.yaml declares hitl.enabled: true.

    An absent or unreadable config.yaml warns instead of skipping silently —
    either case is ambiguous (never shipped / broken
    vs. genuinely non-HITL) and should not look like a clean auto-waiver.
    """
    if not _CONFIG_PATH.exists():
        warnings.warn(
            f"{_CONFIG_PATH} not found — PB-7 skipped without verifying hitl.enabled. "
            "If this template calls interrupt(), ship config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    try:
        import yaml  # pyyaml==6.0.1 — pinned in scaffold/pyproject.toml dependencies

        data = yaml.safe_load(_CONFIG_PATH.read_text())
    except Exception as exc:
        warnings.warn(
            f"{_CONFIG_PATH} could not be read as YAML ({exc}) — PB-7 skipped without "
            "verifying hitl.enabled. If this template calls interrupt(), fix config/config.yaml "
            "before release.",
            stacklevel=2,
        )
        return False
    hitl = (data or {}).get("hitl", {}) if isinstance(data, dict) else None
    if not isinstance(hitl, dict):
        warnings.warn(
            f"{_CONFIG_PATH} does not have the expected 'hitl:' mapping shape — PB-7 skipped "
            "without verifying hitl.enabled. If this template calls interrupt(), fix "
            "config/config.yaml before release.",
            stacklevel=2,
        )
        return False
    return bool(hitl.get("enabled", False))


pytestmark = pytest.mark.skipif(
    not _hitl_enabled(),
    reason="config/config.yaml does not set hitl.enabled: true — PB-7 not applicable",
)

# ---------------------------------------------------------------------------
# ApprovalExecuteNode.execute() calls langgraph.types.interrupt(), which reads
# the active LangGraph run's config (get_config()) to track resume state. A
# bare node(state) call outside any compiled graph has no such config, so
# interrupt() raises a plain RuntimeError there — not GraphInterrupt (this is
# exactly why PB-6's generic per-node probe monkeypatches `interrupt` away for
# this node; see its "ADAPTATION" comment). To exercise the real raise-and-
# propagate path, PB-7-A drives the node through a minimal compiled
# single-node graph with a checkpointer, the only way interrupt() has a real
# run to attach to. PB-7-B's hitl_allowed=False path returns before ever
# calling interrupt(), so it is safe to call the node directly.
# ---------------------------------------------------------------------------

from src.nodes.approval_execute_node import ApprovalExecuteNode  # noqa: E402
from src.schemas.state import State  # noqa: E402
from src.services.change_request_service import ChangeRequestClient  # noqa: E402


def _base_state(**overrides) -> dict:
    """Return a minimal state dict for PB-7 tests.

    Fields match what ApprovalExecuteNode.execute() actually reads: it is
    reached only after ApproverAuthorize + ChangeRiskAssess have already run,
    so approver_authorized / risk_verdict / change_request_id are pre-seeded
    here rather than derived from user_input.
    """
    state = {
        # Framework-managed fields
        "caller_trust_level": "VERIFIED_EXTERNAL",  # matches required_trust_level on this node
        "correlation_id": "pb7-test",
        "node_history": [],
        "error_log": [],
        # HITL fields
        "hitl_allowed": True,  # overridden per test case
        "hitl_count": 0,
        # Domain fields ApprovalExecuteNode.execute() reads
        "change_request_id": "CHG0001234",
        "requesting_user_id": "approver1",
        "urgency": "High",
        "approver_authorized": True,  # bypass the fail-closed unauthorized-reject branch
        "risk_verdict": "ok",  # bypass the deterministic auto-reject branch
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# PB-7-A: interrupt() raises GraphInterrupt and propagates
# ---------------------------------------------------------------------------


def test_pb7_hitl_interrupt_propagates() -> None:
    """PB-7: interrupt() raises GraphInterrupt and propagates (not caught by app boundary).

    Covers PB-7 (first assertion):
      GraphInterrupt reaches the LangGraph engine; status is NOT set to error.

    A single-node graph wrapping ApprovalExecuteNode is compiled with a
    checkpointer (interrupt() requires one) and invoked directly. LangGraph's
    own compiled-graph invoke() translates a GraphInterrupt raised inside a
    node into an `__interrupt__` entry in the returned state rather than
    letting it escape as a Python exception at that boundary — the same
    contract test_pb_hitl_interrupt.py already relies on for the full 3-node
    inner workflow. What PB-7 verifies here is the boundary the docstring
    above names: the interrupt is NOT swallowed by BaseNode.__call__()'s
    generic `except Exception` handler as status=error; it reaches the graph
    engine intact.
    """
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    node = ApprovalExecuteNode(client=ChangeRequestClient(groups={}, risks={}))
    builder = StateGraph(State)
    builder.add_node("approval_execute", node)
    builder.add_edge(START, "approval_execute")
    builder.add_edge("approval_execute", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    state = _base_state(hitl_allowed=True)
    config = {"configurable": {"thread_id": "pb7-a"}}
    result = graph.invoke(state, config=config)

    assert "__interrupt__" in result, f"expected the graph to suspend on interrupt(), got: {result}"
    # The node must not have resolved this as an application error.
    assert result.get("status") not in ("error",), f"interrupt() must not surface as status=error: {result}"


# ---------------------------------------------------------------------------
# PB-7-B: hitl_allowed=False guard prevents deadlock
# ---------------------------------------------------------------------------


def test_pb7_hitl_allowed_false_skips_interrupt() -> None:
    """PB-7 guard: hitl_allowed=False must NOT raise GraphInterrupt (no deadlock).

    Covers the mandatory hitl_allowed guard:
      When hitl_allowed=False, the node must check the flag before calling
      interrupt() and skip the HITL path entirely — it never reaches
      interrupt(), so this can call the node directly with no compiled graph.
    """
    node = ApprovalExecuteNode(client=ChangeRequestClient(groups={}, risks={}))
    state = _base_state(hitl_allowed=False)

    result = node(state)  # BaseNode.__call__ — full S-1..S-4 boundary

    assert result.get("status") == "success", f"expected a resolved (non-error) result, got: {result}"
    assert result.get("approval_decision") == "rejected"
    assert "hitl_allowed=False" in result.get("approval_reason", "")
