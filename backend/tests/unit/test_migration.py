"""Unit tests for SQLite database schema migration and index initialization."""

import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.db.session import init_db


def test_sqlite_pre_phase7_schema_upgrade():
    """Verify pre-Phase 7 SQLite databases are safely upgraded by init_db().

    Simulates a database created before Phase 7:
    - reviews table without 'dropped_findings_count' column
    - findings table without composite indexes
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        # 1. Create a legacy pre-Phase 7 schema using raw sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Legacy reviews table (without dropped_findings_count)
        cursor.execute(
            """
            CREATE TABLE reviews (
                id VARCHAR(36) PRIMARY KEY,
                pr_url VARCHAR(512) NOT NULL,
                owner VARCHAR(255) NOT NULL,
                repo VARCHAR(255) NOT NULL,
                pr_number INTEGER NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'queued',
                current_stage VARCHAR(64),
                error_message TEXT,
                summary TEXT,
                token_usage JSON,
                truncation_info JSON,
                warnings JSON,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )

        # Legacy findings table (without composite indexes)
        cursor.execute(
            """
            CREATE TABLE findings (
                id VARCHAR(36) PRIMARY KEY,
                review_id VARCHAR(36) NOT NULL,
                file_path VARCHAR(1024) NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                title VARCHAR(255) NOT NULL,
                severity VARCHAR(32) NOT NULL,
                category VARCHAR(64) NOT NULL,
                source VARCHAR(32) NOT NULL,
                tool_name VARCHAR(64) NOT NULL,
                rule_id VARCHAR(128) NOT NULL,
                evidence TEXT NOT NULL,
                explanation TEXT NOT NULL,
                suggested_fix TEXT,
                confidence FLOAT NOT NULL DEFAULT 1.0,
                status VARCHAR(32) NOT NULL DEFAULT 'open',
                fingerprint VARCHAR(64) NOT NULL,
                created_at DATETIME,
                updated_at DATETIME,
                FOREIGN KEY (review_id) REFERENCES reviews (id) ON DELETE CASCADE
            )
            """
        )

        # Insert a pre-existing review and finding
        cursor.execute(
            """
            INSERT INTO reviews (id, pr_url, owner, repo, pr_number, status)
            VALUES (
                'legacy-rev-1',
                'https://github.com/o/r/pull/1',
                'o',
                'r',
                1,
                'completed'
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO findings (
                id, review_id, file_path, line_start, line_end, title, severity,
                category, source, tool_name, rule_id, evidence, explanation,
                confidence, status, fingerprint
            )
            VALUES (
                'legacy-find-1', 'legacy-rev-1', 'app.py', 1, 1, 'Legacy issue',
                'high', 'security', 'ai', 'gemini', 'r1', 'x = 1', 'bad',
                0.9, 'open', 'fp-legacy'
            )
            """
        )
        conn.commit()
        conn.close()

        # 2. Run init_db() on the legacy database
        engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}")
        init_db(engine_override=engine)

        # 3. Inspect upgraded database
        with engine.connect() as check_conn:
            inspector = inspect(check_conn)

            # Check dropped_findings_count column was added
            columns = [c["name"] for c in inspector.get_columns("reviews")]
            assert "dropped_findings_count" in columns

            # Check composite indexes exist on findings
            indexes = inspector.get_indexes("findings")
            index_names = {idx["name"] for idx in indexes}
            assert "ix_findings_review_id_status" in index_names
            assert "ix_findings_review_id_category" in index_names

            # Verify existing data was preserved intact
            rev_row = check_conn.execute(
                text(
                    "SELECT id, status, dropped_findings_count "
                    "FROM reviews WHERE id = 'legacy-rev-1'"
                )
            ).fetchone()
            assert rev_row is not None
            assert rev_row[0] == "legacy-rev-1"
            assert rev_row[1] == "completed"
            assert rev_row[2] == 0  # default value applied

            finding_row = check_conn.execute(
                text("SELECT id, title FROM findings WHERE id = 'legacy-find-1'")
            ).fetchone()
            assert finding_row is not None
            assert finding_row[1] == "Legacy issue"

        engine.dispose()
    finally:
        Path(db_path).unlink(missing_ok=True)
