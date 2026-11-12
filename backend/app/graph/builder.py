"""LangGraph workflow builder for PR review orchestration."""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph

from app.graph.nodes import (
    GraphDependencies,
    ai_review,
    fail_review,
    fetch_pr,
    persist_results,
    process_diff,
    retrieve_context,
    static_analysis,
    validate_findings,
)
from app.graph.state import ReviewState

logger = logging.getLogger(__name__)

_COMPILED_GRAPH: Any = None


def _resolve_deps(
    config: RunnableConfig | None, fallback: GraphDependencies
) -> GraphDependencies:
    """Extract runtime GraphDependencies from invocation config or fallback."""
    if config and isinstance(config, dict):
        configurable = config.get("configurable")
        if isinstance(configurable, dict) and "deps" in configurable:
            resolved = configurable["deps"]
            if isinstance(resolved, GraphDependencies):
                return resolved
    return fallback


def get_compiled_graph() -> Any:
    """Return the application-wide compiled LangGraph review state machine."""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_graph().compile()
    return _COMPILED_GRAPH


def build_graph(deps: GraphDependencies | None = None) -> StateGraph:
    """Construct and configure the LangGraph state machine for PR review.

    Workflow:
    fetch_pr -> process_diff -> static_analysis -> retrieve_context ->
    ai_review -> validate_findings -> persist_results -> END

    Every processing node routes conditionally to `fail_review` if `failed == True`.
    """
    active_deps = deps or GraphDependencies()
    graph = StateGraph(ReviewState)

    # Define async node wrappers binding injected dependencies or runtime config
    async def _fetch_pr_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await fetch_pr(state, _resolve_deps(config, active_deps))

    async def _process_diff_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await process_diff(state, _resolve_deps(config, active_deps))

    async def _static_analysis_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await static_analysis(state, _resolve_deps(config, active_deps))

    async def _retrieve_context_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await retrieve_context(state, _resolve_deps(config, active_deps))

    async def _ai_review_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await ai_review(state, _resolve_deps(config, active_deps))

    async def _validate_findings_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        try:
            return await validate_findings(state, _resolve_deps(config, active_deps))
        except Exception as exc:
            logger.error(
                "Unexpected error in validate_findings node for %s: %s",
                state.get("review_id"),
                exc,
            )
            return {
                "failed": True,
                "errors": state.get("errors", [])
                + [
                    {
                        "code": "validation_failed",
                        "message": "Failed to validate review findings.",
                        "stage": "validate_findings",
                    }
                ],
            }

    async def _persist_results_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await persist_results(state, _resolve_deps(config, active_deps))

    async def _fail_review_node(
        state: ReviewState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        return await fail_review(state, _resolve_deps(config, active_deps))

    # Add nodes to graph
    graph.add_node("fetch_pr", _fetch_pr_node)
    graph.add_node("process_diff", _process_diff_node)
    graph.add_node("static_analysis", _static_analysis_node)
    graph.add_node("retrieve_context", _retrieve_context_node)
    graph.add_node("ai_review", _ai_review_node)
    graph.add_node("validate_findings", _validate_findings_node)
    graph.add_node("persist_results", _persist_results_node)
    graph.add_node("fail_review", _fail_review_node)

    # Entry point
    graph.set_entry_point("fetch_pr")

    # Conditional routing after each processing stage
    graph.add_conditional_edges(
        "fetch_pr",
        lambda state: "fail_review" if state.get("failed") else "process_diff",
        {"fail_review": "fail_review", "process_diff": "process_diff"},
    )
    graph.add_conditional_edges(
        "process_diff",
        lambda state: "fail_review" if state.get("failed") else "static_analysis",
        {"fail_review": "fail_review", "static_analysis": "static_analysis"},
    )
    graph.add_conditional_edges(
        "static_analysis",
        lambda state: "fail_review" if state.get("failed") else "retrieve_context",
        {"fail_review": "fail_review", "retrieve_context": "retrieve_context"},
    )
    graph.add_conditional_edges(
        "retrieve_context",
        lambda state: "fail_review" if state.get("failed") else "ai_review",
        {"fail_review": "fail_review", "ai_review": "ai_review"},
    )
    graph.add_conditional_edges(
        "ai_review",
        lambda state: "fail_review" if state.get("failed") else "validate_findings",
        {"fail_review": "fail_review", "validate_findings": "validate_findings"},
    )
    graph.add_conditional_edges(
        "validate_findings",
        lambda state: "fail_review" if state.get("failed") else "persist_results",
        {"fail_review": "fail_review", "persist_results": "persist_results"},
    )
    graph.add_conditional_edges(
        "persist_results",
        lambda state: "fail_review" if state.get("failed") else END,
        {"fail_review": "fail_review", END: END},
    )

    # Failure terminal transition
    graph.add_edge("fail_review", END)

    return graph
