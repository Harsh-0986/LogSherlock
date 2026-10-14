"""Investigation graph assembly (SPEC §8.1).

START -> validate_input -> parse_logs -> normalize_and_group -> rank_errors
      -> select_evidence -> investigate_with_model -> validate_findings
      -> (invalid) bounded_repair -> validate_findings   [bounded, once]
      -> persist_report -> END
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import InvestigationState
from app.logengine.models import Runtime
from app.models.base import BaseChatModel

logger = logging.getLogger("logsherlock.graph")

#: Nodes that correspond to one deterministic stage of the pipeline.
_PARSE_NODES = ("parse_logs", "normalize_and_group", "rank_errors")


def build_graph(model: BaseChatModel):
    """Compile the investigation graph bound to ``model``.

    The model is injected rather than imported so tests can supply a fake
    provider (SPEC §15.3: unit tests must not require a live model).
    """

    async def _investigate(state: InvestigationState) -> dict[str, Any]:
        return await nodes.investigate_with_model(state, model)

    async def _repair(state: InvestigationState) -> dict[str, Any]:
        return await nodes.bounded_repair(state, model)

    def _after_validation(state: InvestigationState) -> str:
        """Route to bounded repair only when a retry could plausibly help.

        A group whose finding was rejected gets exactly one retry. Groups the
        model never answered are not retried: the call already failed or timed
        out, and repeating it just spends the same quota again.
        """
        if state.get("repair_attempted", False):
            return "persist_report"

        if not state.get("rejected_fingerprints"):
            return "persist_report"

        return "bounded_repair"

    graph = StateGraph(InvestigationState)

    graph.add_node("validate_input", nodes.validate_input)
    graph.add_node("parse_logs", nodes.parse_logs)
    graph.add_node("normalize_and_group", nodes.normalize_and_group)
    graph.add_node("rank_errors", nodes.rank_errors)
    graph.add_node("select_evidence", nodes.select_evidence)
    graph.add_node("investigate_with_model", _investigate)
    graph.add_node("validate_findings", nodes.validate_findings)
    graph.add_node("bounded_repair", _repair)
    graph.add_node("persist_report", nodes.persist_report)

    graph.add_edge(START, "validate_input")
    graph.add_edge("validate_input", "parse_logs")
    for stage in _PARSE_NODES[1:]:
        graph.add_edge(_PARSE_NODES[_PARSE_NODES.index(stage) - 1], stage)
    graph.add_edge("rank_errors", "select_evidence")
    graph.add_edge("select_evidence", "investigate_with_model")
    graph.add_edge("investigate_with_model", "validate_findings")
    graph.add_edge("bounded_repair", "validate_findings")
    graph.add_conditional_edges(
        "validate_findings",
        _after_validation,
        {"bounded_repair": "bounded_repair", "persist_report": "persist_report"},
    )
    graph.add_edge("persist_report", END)

    return graph.compile()


#: Hard ceiling on graph steps. The repair loop is already bounded to one
#: attempt; this is the backstop so a misbehaving loop cannot spin forever.
RECURSION_LIMIT = 25


def initial_state(
    *,
    investigation_id: str,
    raw_input: str,
    settings: Any,
    runtime: str = "auto",
    context: str | None = None,
) -> InvestigationState:
    """Build the starting state for one investigation."""
    from app.core.ids import utc_now

    try:
        requested = Runtime(runtime or "auto")
    except ValueError:
        requested = Runtime.AUTO

    return InvestigationState(
        investigation_id=investigation_id,
        raw_input=raw_input,
        settings=settings,
        requested_runtime=requested.value,
        context=context,
        started_at=utc_now().isoformat(),
        status="queued",
        processing_steps=[],
        warnings=[],
        validation_errors=[],
        findings=[],
        repair_attempted=False,
    )


async def stream_investigation(
    model: BaseChatModel,
    *,
    investigation_id: str,
    raw_input: str,
    settings: Any,
    runtime: str = "auto",
    context: str | None = None,
    on_step: Any = None,
):
    """Run the graph, invoking ``on_step`` after every node completes.

    Streaming is what makes progress observable: without it the API can only
    report steps once the whole run has finished, so a two-minute investigation
    looks like a single opaque "running".
    """
    graph = build_graph(model)
    state = initial_state(
        investigation_id=investigation_id,
        raw_input=raw_input,
        settings=settings,
        runtime=runtime,
        context=context,
    )
    final: dict[str, Any] = {}
    # stream_mode="values" yields the FULL state after each node. The default
    # ("updates") yields {node_name: delta}, which is neither progress-reportable
    # nor a usable final result.
    async for update in graph.astream(
        state,
        stream_mode="values",
        config={"recursion_limit": RECURSION_LIMIT},
    ):
        final = update
        if on_step is not None:
            result = on_step(update)
            if hasattr(result, "__await__"):
                await result
    return final


async def run_investigation(
    model: BaseChatModel,
    *,
    investigation_id: str,
    raw_input: str,
    settings: Any,
    runtime: str = "auto",
    context: str | None = None,
) -> dict[str, Any]:
    """Compile and run one investigation end to end.

    Applies :data:`RECURSION_LIMIT` so a misbehaving graph terminates instead of
    looping.
    """
    graph = build_graph(model)
    state = initial_state(
        investigation_id=investigation_id,
        raw_input=raw_input,
        settings=settings,
        runtime=runtime,
        context=context,
    )
    result = await graph.ainvoke(state, config={"recursion_limit": RECURSION_LIMIT})
    return dict(result)


def build_ai_graph(model: BaseChatModel):
    """Compile the AI-only portion of the workflow.

    Retry cannot re-parse: raw logs are not persisted (SPEC 11.5), so the
    deterministic result has to be reused. This graph re-runs just the stages
    that talk to a model, over the groups and evidence already stored.
    """
    async def _investigate(state: InvestigationState) -> dict[str, Any]:
        return await nodes.investigate_with_model(state, model)

    async def _repair(state: InvestigationState) -> dict[str, Any]:
        return await nodes.bounded_repair(state, model)

    def _after_validation(state: InvestigationState) -> str:
        if state.get("repair_attempted", False):
            return "persist_report"
        if not state.get("rejected_fingerprints"):
            return "persist_report"
        return "bounded_repair"

    graph = StateGraph(InvestigationState)
    graph.add_node("investigate_with_model", _investigate)
    graph.add_node("validate_findings", nodes.validate_findings)
    graph.add_node("bounded_repair", _repair)
    graph.add_node("persist_report", nodes.persist_report)

    graph.add_edge(START, "investigate_with_model")
    graph.add_edge("investigate_with_model", "validate_findings")
    graph.add_edge("bounded_repair", "validate_findings")
    graph.add_conditional_edges(
        "validate_findings",
        _after_validation,
        {"bounded_repair": "bounded_repair", "persist_report": "persist_report"},
    )
    graph.add_edge("persist_report", END)
    return graph.compile()


async def run_ai_retry(
    model: BaseChatModel,
    *,
    investigation_id: str,
    groups: list[Any],
    evidence: Any,
    settings: Any,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Re-run only the AI stages against an already-analysed investigation."""
    from app.core.ids import utc_now

    state: InvestigationState = {
        "investigation_id": investigation_id,
        "settings": settings,
        "groups": groups,
        "evidence": evidence,
        "requested_runtime": "auto",
        "started_at": utc_now().isoformat(),
        "status": "running",
        "processing_steps": [],
        "warnings": list(warnings or []),
        "validation_errors": [],
        "repair_attempted": False,
        "raw_findings": [],
        "rejected_fingerprints": [],
        "findings": [],
        "model_response": None,
        "ai_skipped_reason": None,
        "model_call_failures": [],
    }

    result = await build_ai_graph(model).ainvoke(
        state, config={"recursion_limit": RECURSION_LIMIT}
    )
    return dict(result)


async def stream_ai_retry(
    model: BaseChatModel,
    *,
    investigation_id: str,
    groups: list[Any],
    evidence: Any,
    settings: Any,
    warnings: list[str] | None = None,
    on_step: Any = None,
):
    """Run the AI-only graph, reporting each completed step via ``on_step``."""
    from app.core.ids import utc_now

    state: InvestigationState = {
        "investigation_id": investigation_id,
        "settings": settings,
        "groups": groups,
        "evidence": evidence,
        "requested_runtime": "auto",
        "started_at": utc_now().isoformat(),
        "status": "running",
        "processing_steps": [],
        "warnings": list(warnings or []),
        "validation_errors": [],
        "repair_attempted": False,
        "raw_findings": [],
        "rejected_fingerprints": [],
        "findings": [],
        "model_response": None,
        "ai_skipped_reason": None,
        "model_call_failures": [],
    }

    final: dict[str, Any] = {}
    async for update in build_ai_graph(model).astream(
        state, stream_mode="values", config={"recursion_limit": RECURSION_LIMIT}
    ):
        final = update
        if on_step is not None:
            outcome = on_step(update)
            if hasattr(outcome, "__await__"):
                await outcome
    return final
