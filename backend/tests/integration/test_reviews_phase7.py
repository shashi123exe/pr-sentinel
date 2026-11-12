"""Integration tests for Phase 7 review history, filters, and cascade APIs."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

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


def _seed_complete_review(db: Session) -> tuple[str, list[int]]:
    """Helper to seed a completed review with files and multi-severity findings."""
    rev_id = str(uuid.uuid4())
    rev = Review(
        id=rev_id,
        pr_url="https://github.com/acme/my-service/pull/10",
        owner="acme",
        repo="my-service",
        pr_number=10,
        status=ReviewStatus.COMPLETED,
        current_stage="completed",
        dropped_findings_count=2,
    )
    db.add(rev)
    db.flush()

    rf1 = ReviewFile(
        review_id=rev_id,
        path="app/auth.py",
        status="modified",
        additions=10,
        deletions=2,
        language="python",
        patch="@@ -1 +1 @@",
    )
    rf2 = ReviewFile(
        review_id=rev_id,
        path="package-lock.json",
        status="modified",
        additions=100,
        deletions=100,
        skipped_reason="lockfile",
    )
    db.add_all([rf1, rf2])

    f1 = Finding(
        review_id=rev_id,
        file_path="app/auth.py",
        line_start=10,
        line_end=10,
        title="SQL injection risk",
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        source=Source.AI,
        tool_name="gemini",
        rule_id="ai_review",
        evidence="query = f'SELECT *'",
        explanation="Dangerous SQL query",
        confidence=0.98,
        fingerprint="fp_crit_" + rev_id,
        status=FindingStatus.OPEN,
    )
    f2 = Finding(
        review_id=rev_id,
        file_path="app/auth.py",
        line_start=20,
        line_end=20,
        title="Bandit: Hardcoded JWT Secret",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        source=Source.STATIC,
        tool_name="bandit",
        rule_id="B105",
        evidence="secret = 'abc'",
        explanation="Hardcoded credential",
        confidence=1.0,
        fingerprint="fp_high_" + rev_id,
        status=FindingStatus.OPEN,
    )
    f3 = Finding(
        review_id=rev_id,
        file_path="app/utils.py",
        line_start=5,
        line_end=5,
        title="Inefficient list comprehension",
        severity=Severity.LOW,
        category=Category.PERFORMANCE,
        source=Source.AI,
        tool_name="gemini",
        rule_id="ai_review",
        evidence="[x for x in list]",
        explanation="Unnecessary comprehension",
        confidence=0.80,
        fingerprint="fp_low_" + rev_id,
        status=FindingStatus.DISMISSED,
    )
    db.add_all([f1, f2, f3])
    db.commit()

    return rev_id, [f1.id, f2.id, f3.id]


def test_list_reviews_with_filters_and_summary(client: TestClient, db_session: Session):
    """Verify GET /api/reviews filters by status, owner, repo and includes summary."""
    rev_id, _ = _seed_complete_review(db_session)

    # 1. Query all
    resp = client.get("/api/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    item = next(r for r in data["items"] if r["id"] == rev_id)
    assert item["findings_summary"]["critical"] == 1
    assert item["findings_summary"]["high"] == 1
    assert item["findings_summary"]["low"] == 1
    assert item["findings_summary"]["total"] == 3

    # 2. Filter by status=completed
    resp_status = client.get("/api/reviews?status=completed")
    assert resp_status.status_code == 200
    assert any(r["id"] == rev_id for r in resp_status.json()["items"])

    # 3. Filter by owner and repo
    resp_repo = client.get("/api/reviews?owner=acme&repo=my-service")
    assert resp_repo.status_code == 200
    assert any(r["id"] == rev_id for r in resp_repo.json()["items"])

    # 4. Filter with non-matching owner
    resp_none = client.get("/api/reviews?owner=nonexistent_owner")
    assert resp_none.status_code == 200
    assert resp_none.json()["total"] == 0


def test_get_review_with_summary_and_dropped_count(
    client: TestClient, db_session: Session
):
    """Verify GET /api/reviews/{id} returns summary and dropped_findings_count."""
    rev_id, _ = _seed_complete_review(db_session)
    resp = client.get(f"/api/reviews/{rev_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == rev_id
    assert data["dropped_findings_count"] == 2
    assert data["findings_summary"]["critical"] == 1
    assert data["findings_summary"]["high"] == 1
    assert data["findings_summary"]["total"] == 3


def test_list_review_files_with_skipped_filtering_and_counts(
    client: TestClient, db_session: Session
):
    """Verify GET /api/reviews/{id}/files handles include_skipped and counts."""
    rev_id, _ = _seed_complete_review(db_session)

    # 1. include_skipped=True (default): returns both files
    resp_all = client.get(f"/api/reviews/{rev_id}/files?include_skipped=true")
    assert resp_all.status_code == 200
    items_all = resp_all.json()["items"]
    assert len(items_all) == 2
    auth_file = next(f for f in items_all if f["path"] == "app/auth.py")
    assert auth_file["findings_count"] == 2  # f1 and f2 are on app/auth.py

    # 2. include_skipped=False: filters out package-lock.json
    resp_active = client.get(f"/api/reviews/{rev_id}/files?include_skipped=false")
    assert resp_active.status_code == 200
    items_active = resp_active.json()["items"]
    assert len(items_active) == 1
    assert items_active[0]["path"] == "app/auth.py"


def test_list_review_findings_multi_attribute_filtering(
    client: TestClient, db_session: Session
):
    """Verify multi-severity, category, source, status, and confidence filters."""
    rev_id, _ = _seed_complete_review(db_session)

    # 1. Multi-severity filter: critical and high
    resp_sev = client.get(
        f"/api/reviews/{rev_id}/findings?severity=critical&severity=high"
    )
    assert resp_sev.status_code == 200
    assert resp_sev.json()["total"] == 2
    severities = {f["severity"] for f in resp_sev.json()["items"]}
    assert severities == {"critical", "high"}

    # 2. Category filter: performance
    resp_cat = client.get(f"/api/reviews/{rev_id}/findings?category=performance")
    assert resp_cat.status_code == 200
    assert resp_cat.json()["total"] == 1
    assert resp_cat.json()["items"][0]["title"] == "Inefficient list comprehension"

    # 3. Source filter: static
    resp_src = client.get(f"/api/reviews/{rev_id}/findings?source=static")
    assert resp_src.status_code == 200
    assert resp_src.json()["total"] == 1
    assert resp_src.json()["items"][0]["source"] == "static"

    # 4. Status filter: dismissed
    resp_stat = client.get(f"/api/reviews/{rev_id}/findings?status=dismissed")
    assert resp_stat.status_code == 200
    assert resp_stat.json()["total"] == 1
    assert resp_stat.json()["items"][0]["status"] == "dismissed"

    # 5. File path filter: app/auth.py
    resp_file = client.get(f"/api/reviews/{rev_id}/findings?file_path=app/auth.py")
    assert resp_file.status_code == 200
    assert resp_file.json()["total"] == 2

    # 6. Min confidence filter: 0.95
    resp_conf = client.get(f"/api/reviews/{rev_id}/findings?min_confidence=0.95")
    assert resp_conf.status_code == 200
    assert resp_conf.json()["total"] == 2  # 0.98 and 1.0


def test_list_review_findings_sorting(client: TestClient, db_session: Session):
    """Verify sorting by severity, confidence, and file path."""
    rev_id, _ = _seed_complete_review(db_session)

    # 1. Default sort: severity descending (Critical -> High -> Low)
    resp_def = client.get(f"/api/reviews/{rev_id}/findings")
    assert resp_def.status_code == 200
    items = resp_def.json()["items"]
    assert items[0]["severity"] == "critical"
    assert items[1]["severity"] == "high"
    assert items[2]["severity"] == "low"

    # 2. Sort by confidence descending
    resp_conf = client.get(
        f"/api/reviews/{rev_id}/findings?sort_by=confidence&sort_order=desc"
    )
    assert resp_conf.status_code == 200
    conf_items = resp_conf.json()["items"]
    assert conf_items[0]["confidence"] >= conf_items[1]["confidence"]
    assert conf_items[1]["confidence"] >= conf_items[2]["confidence"]

    # 3. Sort by file ascending
    resp_file = client.get(
        f"/api/reviews/{rev_id}/findings?sort_by=file&sort_order=asc"
    )
    assert resp_file.status_code == 200
    f_items = resp_file.json()["items"]
    assert f_items[0]["file_path"] <= f_items[-1]["file_path"]


def test_delete_review_cascades_children(client: TestClient, db_session: Session):
    """Verify DELETE /api/reviews/{id} deletes review and cascades to files/findings."""
    rev_id, _ = _seed_complete_review(db_session)

    resp_del = client.delete(f"/api/reviews/{rev_id}")
    assert resp_del.status_code == 204

    # Subsequent GET returns 404
    resp_get = client.get(f"/api/reviews/{rev_id}")
    assert resp_get.status_code == 404

    # Verify database records are cascade deleted
    db_session.expire_all()
    remaining_files = db_session.scalars(
        select(ReviewFile).where(ReviewFile.review_id == rev_id)
    ).all()
    assert len(remaining_files) == 0

    remaining_findings = db_session.scalars(
        select(Finding).where(Finding.review_id == rev_id)
    ).all()
    assert len(remaining_findings) == 0
