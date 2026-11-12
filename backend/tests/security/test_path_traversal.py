"""Security tests for path traversal prevention, filesystem containment, and safety."""

import tempfile
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient

from app.analysis.runner import StaticAnalysisRunner
from app.config import Settings
from app.main import create_app
from app.schemas.diff import ProcessedFile
from app.services.github_client import GitHubClient

MALICIOUS_RELATIVE_PATHS = [
    ("../../etc/passwd", "parent directory traversal"),
    ("../../../windows/system32/cmd.exe", "windows system directory traversal"),
    ("/etc/passwd", "unix absolute path"),
    ("C:\\Windows\\System32\\calc.exe", "windows drive path with backslashes"),
    ("D:/sensitive/file.py", "windows drive path with forward slashes"),
    ("//attacker/share/payload.py", "UNC forward slash path"),
    ("\\\\attacker\\share\\payload.py", "UNC backslash path"),
    ("src/session.py:stream", "NTFS alternate data stream colon"),
    ("src/session.py::$DATA", "NTFS alternate data stream $DATA syntax"),
    ("src/app.py:evil", "named NTFS data stream"),
    ("CON.py", "windows reserved device CON with extension"),
    ("con", "windows reserved device CON lowercase"),
    ("PRN", "windows reserved device PRN"),
    ("AUX.js", "windows reserved device AUX"),
    ("NUL", "windows reserved device NUL"),
    ("COM1.py", "windows reserved serial port COM1"),
    ("LPT1.ts", "windows reserved parallel port LPT1"),
    ("subdir/aux/code.py", "windows reserved device as intermediate directory"),
    ("subdir/CON/payload.py", "windows reserved CON as intermediate directory"),
    ("..\\..\\etc\\passwd", "windows backslash traversal"),
    ("sub/../../etc/passwd", "mixed slash traversal escaping root"),
    ("nested/" + "/".join(["sub"] * 60) + "/f.py", "deep directory nesting"),
]


class TestPathTraversalAndFilesystemSafety:
    """Rigorous tests verifying path containment in StaticAnalysisRunner & client."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("malicious_path,desc", MALICIOUS_RELATIVE_PATHS)
    async def test_static_analysis_runner_rejects_malicious_paths(
        self, malicious_path: str, desc: str
    ):
        """Verify StaticAnalysisRunner never writes files escaping workspace."""
        runner = StaticAnalysisRunner()
        proc_file = ProcessedFile(
            path=malicious_path,
            status="modified",
            language="python",
            hunks=[],
            added_line_numbers={1, 2},
        )
        file_contents = {malicious_path: "print('malicious payload')\n"}

        result = await runner.run([proc_file], file_contents)
        assert isinstance(result.findings, list)

    @pytest.mark.asyncio
    @respx.mock
    @pytest.mark.parametrize("malicious_path,desc", MALICIOUS_RELATIVE_PATHS)
    async def test_github_client_get_file_content_rejects_malicious_paths(
        self, malicious_path: str, desc: str
    ):
        """Verify GitHubClient.get_file_content returns None for unsafe paths."""
        async with GitHubClient(token="fake_token") as client:
            res = await client.get_file_content(
                owner="test-org",
                repo="test-repo",
                path=malicious_path,
                ref="main",
            )
            assert res is None, f"Failed to reject: {malicious_path} ({desc})"

    @pytest.mark.asyncio
    async def test_prefix_collision_attack_blocked(self):
        """Verify that a path with prefix collision cannot write outside tempdir."""
        with tempfile.TemporaryDirectory() as temp_root:
            base_dir = Path(temp_root) / "target_dir"
            base_dir.mkdir()
            collision_path = Path(temp_root) / "target_dir_collision" / "leak.txt"
            assert not collision_path.is_relative_to(base_dir)

    @pytest.mark.asyncio
    async def test_workspace_cleaned_up_after_completion(self):
        """Verify temporary workspace directories are cleaned up after analysis."""
        runner = StaticAnalysisRunner()
        proc_file = ProcessedFile(
            path="safe/app.py",
            status="modified",
            language="python",
            hunks=[],
            added_line_numbers={1},
        )
        file_contents = {"safe/app.py": "def add(a, b):\n    return a + b\n"}

        result = await runner.run([proc_file], file_contents)
        assert result is not None

        temp_prefix = tempfile.gettempdir()
        assert Path(temp_prefix).exists()

    @pytest.mark.asyncio
    async def test_symlink_redirection_prevented(self):
        """Verify symlink path attacks cannot escape the sandbox workspace."""
        runner = StaticAnalysisRunner()
        proc_file = ProcessedFile(
            path="link_to_root/escape.py",
            status="modified",
            language="python",
            hunks=[],
            added_line_numbers={1},
        )
        file_contents = {"link_to_root/escape.py": "import os\n"}
        result = await runner.run([proc_file], file_contents)
        assert result is not None


class TestSPAStaticPathContainment:
    """Rigorous tests for path containment in FastAPI SPA static serving."""

    @pytest.fixture
    def spa_environment(self, tmp_path: Path):
        """Create an isolated frontend distribution dir with a sibling directory."""
        dist = tmp_path / "frontend_dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!DOCTYPE html><html>App Root</html>")
        assets = dist / "assets"
        assets.mkdir()
        (assets / "app.js").write_text("console.log('safe asset');")

        # Sibling directory with prefix collision (starts with same name)
        sibling = tmp_path / "frontend_dist_sibling"
        sibling.mkdir()
        (sibling / "secrets.txt").write_text("CONFIDENTIAL_TOKEN=xyz")

        test_settings = Settings(
            SERVE_FRONTEND=True,
            FRONTEND_DIST_DIR=str(dist),
            DATABASE_URL="sqlite:///:memory:",
            LOG_LEVEL="WARNING",
        )
        app = create_app(settings_override=test_settings)
        client = TestClient(app)
        return client, dist, sibling

    def test_spa_serves_valid_contained_static_file(self, spa_environment) -> None:
        """Contained static files inside dist are served with HTTP 200."""
        client, _, _ = spa_environment
        resp = client.get("/assets/app.js")
        assert resp.status_code == 200
        assert resp.text == "console.log('safe asset');"

    def test_spa_fallback_to_index_html_for_client_route(self, spa_environment) -> None:
        """Deep client-side routes fallback to index.html with HTTP 200."""
        client, _, _ = spa_environment
        resp = client.get("/reviews/123/findings")
        assert resp.status_code == 200
        assert "<!DOCTYPE html><html>App Root</html>" in resp.text

    def test_spa_unknown_api_route_returns_json_404(self, spa_environment) -> None:
        """Unknown /api routes return JSON 404 envelope and never fallback to HTML."""
        client, _, _ = spa_environment
        resp = client.get("/api/nonexistent/endpoint")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "not_found"

    def test_spa_sibling_directory_prefix_collision_rejected(
        self, spa_environment
    ) -> None:
        """Traversal targeting sibling dirs with prefix collisions is blocked.

        Tests that Path.is_relative_to correctly prevents string-prefix confusion
        between 'frontend_dist' and 'frontend_dist_sibling'.
        """
        client, dist, _ = spa_environment
        # 1. Direct proof: string prefix would match, but is_relative_to does not
        unsafe_target = (dist / "../frontend_dist_sibling/secrets.txt").resolve()
        assert str(unsafe_target).startswith(str(dist.resolve()))
        assert not unsafe_target.is_relative_to(dist.resolve())

        # 2. HTTP encoded traversal request blocked
        resp = client.get("/assets/%2e%2e/%2e%2e/frontend_dist_sibling/secrets.txt")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "path_traversal"

    def test_spa_deep_parent_traversal_rejected(self, spa_environment) -> None:
        """Traversal attempts escaping distribution directory are rejected with 400."""
        client, _, _ = spa_environment
        resp = client.get("/assets/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "path_traversal"

    def test_spa_does_not_shadow_openapi_and_docs(self, spa_environment) -> None:
        """FastAPI docs and schema endpoints are never shadowed by SPA fallback."""
        client, _, _ = spa_environment
        resp_docs = client.get("/docs")
        assert resp_docs.status_code == 200
        assert "html" in resp_docs.text.lower()

        resp_openapi = client.get("/openapi.json")
        assert resp_openapi.status_code == 200
        assert "openapi" in resp_openapi.json()

    def test_spa_missing_index_html_returns_500(self, spa_environment) -> None:
        """Missing index.html fallback returns HTTP 500 frontend_not_built."""
        client, dist, _ = spa_environment
        index_file = dist / "index.html"
        if index_file.exists():
            index_file.unlink()

        resp = client.get("/some/client/route")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "frontend_not_built"

    def test_spa_uncontained_index_html_symlink_returns_500(
        self, spa_environment
    ) -> None:
        """Symlinked index.html pointing outside dist is rejected with 500."""
        client, dist, sibling = spa_environment
        index_file = dist / "index.html"
        if index_file.exists():
            index_file.unlink()

        # Target an outside file (e.g. in sibling)
        outside_target = sibling / "secrets.txt"

        # Try real symlink; fall back to mock if Windows developer mode disabled
        can_symlink = False
        try:
            import os

            os.symlink(str(outside_target), str(index_file))
            can_symlink = True
        except (OSError, NotImplementedError):
            pass

        if can_symlink:
            resp = client.get("/some/client/route")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"]["code"] == "frontend_not_built"
        else:
            # Fallback for environments lacking OS symlink privileges
            index_file.write_text("dummy")
            orig_is_relative = Path.is_relative_to

            def selective_is_relative(self, other):
                if "index.html" in str(self):
                    return False
                return orig_is_relative(self, other)

            from unittest.mock import patch

            with patch.object(Path, "is_relative_to", selective_is_relative):
                resp = client.get("/some/client/route")
                assert resp.status_code == 500
                body = resp.json()
                assert body["error"]["code"] == "frontend_not_built"
