"""Security tests for SQL injection prevention and input whitelisting."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Review


class TestSQLInjectionPrevention:
    """Security tests verifying parameterized queries and whitelist enforcement."""

    def test_sql_injection_payloads_in_review_id_route(self, client: TestClient):
        """Verify injection strings in review ID return 404 or 422, not SQL error."""
        malicious_ids = [
            "' OR 1=1 --",
            "1'; DROP TABLE reviews; --",
            "1 UNION SELECT * FROM reviews --",
            "admin'--",
            "' OR 'x'='x",
            "00000000-0000-0000-0000-000000000000' OR '1'='1",
        ]
        for mid in malicious_ids:
            # 1. GET /api/reviews/{id}
            resp = client.get(f"/api/reviews/{mid}")
            assert resp.status_code in {404, 422}
            assert "error" in resp.json()

            # 2. GET /api/reviews/{id}/findings
            resp_findings = client.get(f"/api/reviews/{mid}/findings")
            assert resp_findings.status_code in {404, 422}
            assert "error" in resp_findings.json()

    def test_sort_by_sql_injection_blocked_by_pattern_whitelist(
        self, client: TestClient
    ):
        """Verify sort_by parameter rejects SQL injection payloads with 422."""
        injection_sort_values = [
            "severity; DROP TABLE findings; --",
            "severity ASC, (SELECT * FROM reviews)",
            "file; DELETE FROM reviews",
            "' OR 1=1 --",
            "id",
            "created_at; --",
        ]
        for val in injection_sort_values:
            resp = client.get(
                "/api/reviews/00000000-0000-0000-0000-000000000000/findings",
                params={"sort_by": val},
            )
            assert resp.status_code == 422, f"Failed to reject sort_by: {val}"
            err = resp.json()["error"]
            assert err["code"] == "validation_error"

    def test_sort_order_sql_injection_blocked_by_pattern_whitelist(
        self, client: TestClient
    ):
        """Verify sort_order rejects non-whitelisted SQL payloads with 422."""
        injection_orders = [
            "asc; DROP TABLE reviews",
            "desc; SELECT 1",
            "asc, desc",
            "'",
            "1",
        ]
        for val in injection_orders:
            resp = client.get(
                "/api/reviews/00000000-0000-0000-0000-000000000000/findings",
                params={"sort_order": val},
            )
            assert resp.status_code == 422, f"Failed to reject sort_order: {val}"

    def test_pagination_limits_enforced_against_dos(self, client: TestClient):
        """Verify page_size cannot exceed 100 or be negative."""
        for invalid_size in [-1, 0, 101, 100000]:
            resp = client.get(
                "/api/reviews/00000000-0000-0000-0000-000000000000/findings",
                params={"page_size": invalid_size},
            )
            assert resp.status_code == 422

    def test_sqlalchemy_orm_parameterization_safety(self, db_session: Session):
        """Verify direct ORM queries safely handle arbitrary quotes and characters."""
        malicious_input = "malicious_title'; DROP TABLE reviews; --"

        # Query using standard parameterized filter
        stmt = select(Review).where(Review.pr_title == malicious_input)
        result = db_session.scalars(stmt).all()
        # Query completes cleanly with 0 results, table remains intact
        assert len(result) == 0

        # Check table still exists
        count = db_session.query(Review).count()
        assert count >= 0
