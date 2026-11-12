"""Abstract base interface for repository context retrieval."""

from abc import ABC, abstractmethod

from app.schemas.context import ContextRequest, ContextResult


class ContextRetriever(ABC):
    """Abstract interface for extracting relevant repository context snippets.

    Architectural Note:
    The Phase 4 implementation (KeywordSymbolRetriever) utilizes AST inspection,
    symbol tracking, and surrounding diff hunk ranges.
    This interface serves as a clean future extension point for semantic or
    vector-based context retrievers (e.g. EmbeddingContextRetriever backed by pgvector).
    """

    @abstractmethod
    async def retrieve(self, request: ContextRequest) -> ContextResult:
        """Retrieve relevant context snippets for the provided PR files.

        Args:
            request: Specification of code files, head SHA, and repository coordinates.

        Returns:
            ContextResult containing snippets and truncation status.
        """
        raise NotImplementedError
