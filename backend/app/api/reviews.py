"""Pull request review management API endpoints."""

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Query,
    Request,
    Response,
)
from fastapi import (
    status as http_status,
)
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import (
    Category,
    Finding,
    FindingStatus,
    Review,
    ReviewFile,
    ReviewStatus,
    Severity,
    Source,
)
from app.db.session import SessionLocal, get_db
from app.errors import AppError
from app.graph.nodes import GraphDependencies
from app.rate_limit import limiter
from app.schemas.common import Page
from app.schemas.review import (
    FindingOut,
    FindingsSummary,
    ReviewCreate,
    ReviewCreateResponse,
    ReviewFileOut,
    ReviewListItem,
    ReviewOut,
)
from app.services.review_runner import run_review
from app.services.url_parser import parse_pr_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reviews", tags=["reviews"])

# Process-level mutex ensuring atomic rolling 24-hour limit checks across threads
_review_limit_lock = threading.Lock()


@router.post(
    "",
    response_model=ReviewCreateResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
@limiter.limit("5/minute")
def create_review(
    request: Request,
    response: Response,
    review_in: ReviewCreate,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReviewCreateResponse:
    """Submit a GitHub Pull Request for asynchronous review.

    Validates the GitHub PR URL, commits a queued Review record, schedules
    the LangGraph review workflow via FastAPI BackgroundTasks, and returns 202.
    """
    # 1. Strictly validate and parse GitHub Pull Request URL
    parsed_pr = parse_pr_url(review_in.pr_url)

    # 2. Concurrency-safe rolling 24-hour review limit check & atomic insertion
    max_reviews = settings.MAX_REVIEWS_PER_DAY

    with _review_limit_lock:
        if max_reviews > 0:
            cutoff = datetime.now(UTC) - timedelta(hours=24)
            active_count = (
                db.scalar(
                    select(func.count(Review.id)).where(
                        Review.created_at >= cutoff,
                    )
                )
                or 0
            )
            if active_count >= max_reviews:
                logger.warning(
                    "Daily review limit reached: %d reviews in rolling 24h (limit=%d)",
                    active_count,
                    max_reviews,
                )
                raise AppError(
                    code="daily_limit_reached",
                    message=(
                        f"Daily review limit reached ({max_reviews} reviews per "
                        "24 hours). Please try again later."
                    ),
                    status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
                )

        # Persist queued review record with canonical URL and parsed components
        review_id = str(uuid.uuid4())
        new_review = Review(
            id=review_id,
            pr_url=parsed_pr.canonical_url,
            owner=parsed_pr.owner,
            repo=parsed_pr.repo,
            pr_number=parsed_pr.number,
            status=ReviewStatus.QUEUED,
            current_stage="queued",
            warnings=[],
            created_at=datetime.now(UTC),
        )
        db.add(new_review)
        db.commit()
        db.refresh(new_review)

    # 3. Schedule background review execution with app's session factory
    session_factory = (
        getattr(request.app.state, "session_factory", SessionLocal)
        if hasattr(request, "app") and hasattr(request.app, "state")
        else SessionLocal
    )
    compiled_graph = (
        getattr(request.app.state, "compiled_graph", None)
        if hasattr(request, "app") and hasattr(request.app, "state")
        else None
    )
    background_tasks.add_task(
        run_review,
        review_id,
        deps=GraphDependencies(session_factory=session_factory),
        compiled_app=compiled_graph,
    )

    logger.info(
        "Created queued review [%s] for %s/%s#%d (%s)",
        review_id,
        parsed_pr.owner,
        parsed_pr.repo,
        parsed_pr.number,
        parsed_pr.canonical_url,
    )
    return ReviewCreateResponse(id=review_id, status=ReviewStatus.QUEUED)


def _get_findings_summaries(
    db: Session, review_ids: list[str]
) -> dict[str, FindingsSummary]:
    """Efficiently calculate finding summaries for a collection of review IDs."""
    if not review_ids:
        return {}

    summaries: dict[str, FindingsSummary] = {
        rid: FindingsSummary() for rid in review_ids
    }

    counts_query = (
        select(Finding.review_id, Finding.severity, func.count())
        .where(Finding.review_id.in_(review_ids))
        .group_by(Finding.review_id, Finding.severity)
    )
    rows = db.execute(counts_query).all()

    for rev_id, sev, count in rows:
        if rev_id not in summaries:
            continue
        summary = summaries[rev_id]
        sev_val = sev.value if hasattr(sev, "value") else str(sev).lower()
        if sev_val == "critical":
            summary.critical += count
        elif sev_val == "high":
            summary.high += count
        elif sev_val == "medium":
            summary.medium += count
        elif sev_val == "low":
            summary.low += count
        summary.total += count

    return summaries


def _get_single_findings_summary(db: Session, review_id: str) -> FindingsSummary:
    """Calculate findings summary for a single review record."""
    summary = FindingsSummary()
    counts_query = (
        select(Finding.severity, func.count())
        .where(Finding.review_id == review_id)
        .group_by(Finding.severity)
    )
    rows = db.execute(counts_query).all()
    for sev, count in rows:
        sev_val = sev.value if hasattr(sev, "value") else str(sev).lower()
        if sev_val == "critical":
            summary.critical += count
        elif sev_val == "high":
            summary.high += count
        elif sev_val == "medium":
            summary.medium += count
        elif sev_val == "low":
            summary.low += count
        summary.total += count
    return summary


@router.get(
    "", response_model=Page[ReviewListItem], status_code=http_status.HTTP_200_OK
)
def list_reviews(
    db: Annotated[Session, Depends(get_db)],
    review_status: Annotated[
        ReviewStatus | None,
        Query(alias="status", description="Filter by review status"),
    ] = None,
    owner: Annotated[
        str | None, Query(description="Filter by repository owner")
    ] = None,
    repo: Annotated[str | None, Query(description="Filter by repository name")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
) -> Page[ReviewListItem]:
    """Retrieve list of reviews with finding summaries, ordered newest first."""
    base_query = select(Review)
    count_query = select(func.count()).select_from(Review)

    if review_status is not None:
        base_query = base_query.where(Review.status == review_status)
        count_query = count_query.where(Review.status == review_status)
    if owner is not None:
        base_query = base_query.where(Review.owner == owner)
        count_query = count_query.where(Review.owner == owner)
    if repo is not None:
        base_query = base_query.where(Review.repo == repo)
        count_query = count_query.where(Review.repo == repo)

    total = db.scalar(count_query) or 0
    offset = (page - 1) * page_size
    records = db.scalars(
        base_query.order_by(Review.created_at.desc()).offset(offset).limit(page_size)
    ).all()

    rev_ids = [r.id for r in records]
    summaries = _get_findings_summaries(db, rev_ids)

    items: list[ReviewListItem] = []
    for r in records:
        item = ReviewListItem.model_validate(r)
        item.findings_summary = summaries.get(r.id, FindingsSummary())
        items.append(item)

    return Page[ReviewListItem](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{id}", response_model=ReviewOut, status_code=http_status.HTTP_200_OK)
def get_review(
    id: str,
    db: Annotated[Session, Depends(get_db)],
) -> ReviewOut:
    """Retrieve details of a single review by UUID, including findings summary."""
    review = db.scalar(select(Review).where(Review.id == id))
    if review is None:
        raise AppError(
            code="review_not_found",
            message=f"Review '{id}' not found.",
            status_code=http_status.HTTP_404_NOT_FOUND,
        )
    f_summary = _get_single_findings_summary(db, id)
    out = ReviewOut.model_validate(review)
    out.findings_summary = f_summary
    return out


@router.get(
    "/{id}/findings",
    response_model=Page[FindingOut],
    status_code=http_status.HTTP_200_OK,
)
def list_review_findings(
    id: str,
    db: Annotated[Session, Depends(get_db)],
    severity: Annotated[
        list[Severity] | None, Query(description="Filter by severity")
    ] = None,
    category: Annotated[
        Category | None, Query(description="Filter by category")
    ] = None,
    source: Annotated[Source | None, Query(description="Filter by source")] = None,
    finding_status: Annotated[
        FindingStatus | None,
        Query(alias="status", description="Filter by status"),
    ] = None,
    file_path: Annotated[str | None, Query(description="Filter by file path")] = None,
    min_confidence: Annotated[
        float | None, Query(ge=0.0, le=1.0, description="Minimum confidence")
    ] = None,
    sort_by: Annotated[
        str,
        Query(
            pattern="^(severity|confidence|file)$",
            description="Sort by field",
        ),
    ] = "severity",
    sort_order: Annotated[
        str | None,
        Query(pattern="^(asc|desc)$", description="Sort order"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> Page[FindingOut]:
    """Retrieve validated findings with multi-attribute filtering and sorting."""
    review = db.scalar(select(Review).where(Review.id == id))
    if review is None:
        raise AppError(
            code="review_not_found",
            message=f"Review '{id}' not found.",
            status_code=http_status.HTTP_404_NOT_FOUND,
        )

    base_query = select(Finding).where(Finding.review_id == id)
    count_query = (
        select(func.count()).select_from(Finding).where(Finding.review_id == id)
    )

    if severity:
        base_query = base_query.where(Finding.severity.in_(severity))
        count_query = count_query.where(Finding.severity.in_(severity))
    if category is not None:
        base_query = base_query.where(Finding.category == category)
        count_query = count_query.where(Finding.category == category)
    if source is not None:
        base_query = base_query.where(Finding.source == source)
        count_query = count_query.where(Finding.source == source)
    if finding_status is not None:
        base_query = base_query.where(Finding.status == finding_status)
        count_query = count_query.where(Finding.status == finding_status)
    if file_path is not None:
        base_query = base_query.where(Finding.file_path == file_path)
        count_query = count_query.where(Finding.file_path == file_path)
    if min_confidence is not None:
        base_query = base_query.where(Finding.confidence >= min_confidence)
        count_query = count_query.where(Finding.confidence >= min_confidence)

    total = db.scalar(count_query) or 0

    if sort_order is None:
        effective_order = "desc" if sort_by in ("severity", "confidence") else "asc"
    else:
        effective_order = sort_order

    # Sorting
    if sort_by == "severity":
        sev_rank = case(
            (Finding.severity == Severity.CRITICAL, 0),
            (Finding.severity == Severity.HIGH, 1),
            (Finding.severity == Severity.MEDIUM, 2),
            (Finding.severity == Severity.LOW, 3),
            else_=4,
        )
        if effective_order == "desc":
            base_query = base_query.order_by(
                sev_rank.asc(), Finding.confidence.desc(), Finding.id.asc()
            )
        else:
            base_query = base_query.order_by(
                sev_rank.desc(), Finding.confidence.asc(), Finding.id.asc()
            )
    elif sort_by == "confidence":
        order_col = (
            Finding.confidence.desc()
            if effective_order == "desc"
            else Finding.confidence.asc()
        )
        base_query = base_query.order_by(order_col, Finding.id.asc())
    elif sort_by == "file":
        order_col = (
            Finding.file_path.asc()
            if effective_order == "asc"
            else Finding.file_path.desc()
        )
        base_query = base_query.order_by(
            order_col, Finding.line_start.asc(), Finding.id.asc()
        )
    else:
        base_query = base_query.order_by(Finding.id.asc())

    offset = (page - 1) * page_size
    records = db.scalars(base_query.offset(offset).limit(page_size)).all()
    items = [FindingOut.model_validate(f) for f in records]

    return Page[FindingOut](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{id}/files",
    response_model=Page[ReviewFileOut],
    status_code=http_status.HTTP_200_OK,
)
def list_review_files(
    id: str,
    db: Annotated[Session, Depends(get_db)],
    include_skipped: Annotated[bool, Query(description="Include skipped files")] = True,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> Page[ReviewFileOut]:
    """Retrieve paginated list of touched files with findings counts."""
    review = db.scalar(select(Review).where(Review.id == id))
    if review is None:
        raise AppError(
            code="review_not_found",
            message=f"Review '{id}' not found.",
            status_code=http_status.HTTP_404_NOT_FOUND,
        )

    base_query = select(ReviewFile).where(ReviewFile.review_id == id)
    count_query = (
        select(func.count()).select_from(ReviewFile).where(ReviewFile.review_id == id)
    )

    if not include_skipped:
        base_query = base_query.where(ReviewFile.skipped_reason.is_(None))
        count_query = count_query.where(ReviewFile.skipped_reason.is_(None))

    total = db.scalar(count_query) or 0
    offset = (page - 1) * page_size
    records = db.scalars(
        base_query.order_by(ReviewFile.id.asc()).offset(offset).limit(page_size)
    ).all()

    # Pre-calculate findings count per file path
    counts_query = (
        select(Finding.file_path, func.count())
        .where(Finding.review_id == id)
        .group_by(Finding.file_path)
    )
    findings_per_file = dict(db.execute(counts_query).all())

    items: list[ReviewFileOut] = []
    for f in records:
        out = ReviewFileOut.model_validate(f)
        out.findings_count = findings_per_file.get(f.path, 0)
        items.append(out)

    return Page[ReviewFileOut](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete(
    "/{id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_review(
    id: str,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Delete a review and cascade delete associated files and findings."""
    review = db.scalar(select(Review).where(Review.id == id))
    if review is None:
        raise AppError(
            code="review_not_found",
            message=f"Review '{id}' not found.",
            status_code=http_status.HTTP_404_NOT_FOUND,
        )

    db.delete(review)
    db.commit()
    logger.info("Deleted review [%s] and cascaded children.", id)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
