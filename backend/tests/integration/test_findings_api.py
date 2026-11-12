"""Integration tests for finding management and triage API endpoints."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import (
    Category,
    Finding,
    FindingStatus,
    Review,
    ReviewStatus,
    Severity,
    Source,
)


def _seed_finding(
    db: Session, initial_status: FindingStatus = FindingStatus.OPEN
) -> Finding:
    """Helper to seed a review and finding in test database."""
    rev_id = str(uuid.uuid4())
    rev = Review(
        id=rev_id,
        pr_url="https://github.com/owner/repo/pull/1",
        owner="owner",
        repo="repo",
        pr_number=1,
        status=ReviewStatus.COMPLETED,
        current_stage="completed",
    )
    db.add(rev)
    db.flush()

    finding = Finding(
        review_id=rev_id,
        file_path="app/auth.py",
        line_start=15,
        line_end=15,
        title="Insecure password hash",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        source=Source.AI,
        tool_name="gemini",
        rule_id="ai_review",
        evidence="hash = md5(password)",
        explanation="MD5 is cryptographically broken.",
        suggested_fix="Use bcrypt or argon2.",
        confidence=0.95,
        fingerprint="fp_" + uuid.uuid4().hex,
        status=initial_status,
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding


def test_get_finding_success(client: TestClient, db_session: Session):
    """Verify GET /api/findings/{id} returns 200 with full finding details."""
    finding = _seed_finding(db_session)
    resp = client.get(f"/api/findings/{finding.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == finding.id
    assert data["title"] == "Insecure password hash"
    assert data["severity"] == "high"
    assert data["category"] == "security"
    assert data["source"] == "ai"
    assert data["status"] == "open"
    assert data["confidence"] == 0.95


def test_get_finding_not_found(client: TestClient):
    """Verify GET /api/findings/{id} returns 404 for nonexistent finding."""
    resp = client.get("/api/findings/999999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "finding_not_found"


def test_patch_finding_status_success(client: TestClient, db_session: Session):
    """Verify PATCH /api/findings/{id} updates triage status and updated_at."""
    finding = _seed_finding(db_session, initial_status=FindingStatus.OPEN)
    assert finding.status == FindingStatus.OPEN

    # 1. Update to approved
    resp = client.patch(
        f"/api/findings/{finding.id}",
        json={"status": "approved"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["updated_at"] is not None

    # 2. Update to dismissed
    resp2 = client.patch(
        f"/api/findings/{finding.id}",
        json={"status": "dismissed"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "dismissed"


def test_patch_finding_rejects_invalid_status(client: TestClient, db_session: Session):
    """Verify PATCH /api/findings/{id} returns 422 for unallowed status values."""
    finding = _seed_finding(db_session)
    resp = client.patch(
        f"/api/findings/{finding.id}",
        json={"status": "invalid_status_value"},
    )
    assert resp.status_code == 422


def test_patch_finding_rejects_extra_fields(client: TestClient, db_session: Session):
    """Verify PATCH /api/findings/{id} rejects extra immutable fields with 422."""
    finding = _seed_finding(db_session)
    resp = client.patch(
        f"/api/findings/{finding.id}",
        json={"status": "approved", "severity": "low", "title": "Tampered"},
    )
    assert resp.status_code == 422


def test_patch_finding_not_found(client: TestClient):
    """Verify PATCH /api/findings/{id} returns 404 for nonexistent finding."""
    resp = client.patch(
        "/api/findings/999999",
        json={"status": "approved"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "finding_not_found"
