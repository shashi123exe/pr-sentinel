"""Security tests for code execution prevention and tool isolation."""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from app.analysis.eslint_runner import build_eslint_command, get_eslint_paths
from app.analysis.ruff_runner import build_ruff_command
from app.analysis.runner import REPO_CONFIG_FILES, StaticAnalysisRunner
from app.schemas.diff import ProcessedFile


class TestCodeExecutionPrevention:
    """Verifies that no dynamic code execution paths exist in backend."""

    def test_no_dangerous_builtins_or_modules_in_backend_ast(self):
        """Scan all Python ASTs in app/ to verify no exec, eval, or pickle calls."""
        backend_app = Path(__file__).resolve().parent.parent.parent / "app"
        assert backend_app.exists()

        forbidden_calls = {"eval", "exec", "__import__"}
        forbidden_modules = {"pickle", "shelve", "marshal", "pty"}

        for py_file in backend_app.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                # Verify no direct call to eval(), exec(), etc.
                if isinstance(node, ast.Call):
                    if (
                        isinstance(node.func, ast.Name)
                        and node.func.id in forbidden_calls
                    ):
                        pytest.fail(
                            f"Forbidden call {node.func.id}() in "
                            f"{py_file.name}:{node.lineno}"
                        )
                # Verify no import of unsafe serialization or terminal modules
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            root_mod = alias.name.split(".")[0]
                            assert root_mod not in forbidden_modules, (
                                f"Forbidden {root_mod} in {py_file.name}:{node.lineno}"
                            )
                    elif node.module:
                        root_mod = node.module.split(".")[0]
                        assert root_mod not in forbidden_modules, (
                            f"Forbidden {root_mod} in {py_file.name}:{node.lineno}"
                        )

    def test_no_shell_true_in_backend_subprocesses(self):
        """Verify no subprocess invocation in app uses shell=True."""
        backend_app = Path(__file__).resolve().parent.parent.parent / "app"
        for py_file in backend_app.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check keyword arguments for shell=True
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            if (
                                isinstance(kw.value, ast.Constant)
                                and kw.value.value is True
                            ):
                                pytest.fail(
                                    f"shell=True found in {py_file.name}:{node.lineno}"
                                )

    def test_eslint_command_uses_isolated_config(self):
        """Verify build_eslint_command uses --no-config-lookup and fixed -c config."""
        node_exe, eslint_bin, eslint_config = get_eslint_paths()
        target_dir = Path("/tmp/mock_target")
        cmd = build_eslint_command(
            node_exe or "node", eslint_bin, eslint_config, target_dir
        )

        assert "--no-config-lookup" in cmd
        assert "-c" in cmd
        c_idx = cmd.index("-c")
        # Target config must be our internal tool config, not a repo-supplied path
        assert cmd[c_idx + 1] == str(eslint_config)
        assert cmd[-1] == str(target_dir)

    def test_ruff_command_uses_isolated_flag(self):
        """Verify build_ruff_command enforces --isolated to ignore repo configs."""
        target_dir = Path("/tmp/mock_target")
        cmd = build_ruff_command(target_dir)

        assert "--isolated" in cmd
        assert "check" in cmd
        assert cmd[-1] == str(target_dir)

    @pytest.mark.asyncio
    async def test_repo_config_files_excluded_from_workspace(self):
        """Verify StaticAnalysisRunner excludes malicious repo configs from disk."""
        runner = StaticAnalysisRunner()

        # Create malicious config files attempting code execution on load
        malicious_files = [
            ProcessedFile(
                path="eslint.config.js",
                status="added",
                language="javascript",
                hunks=[],
                added_line_numbers={1},
            ),
            ProcessedFile(
                path="pyproject.toml",
                status="added",
                language="python",
                hunks=[],
                added_line_numbers={1},
            ),
            ProcessedFile(
                path="ruff.toml",
                status="added",
                language="python",
                hunks=[],
                added_line_numbers={1},
            ),
            ProcessedFile(
                path="package.json",
                status="added",
                language="json",
                hunks=[],
                added_line_numbers={1},
            ),
            ProcessedFile(
                path="src/safe_module.py",
                status="added",
                language="python",
                hunks=[],
                added_line_numbers={1},
            ),
        ]

        contents = {
            "eslint.config.js": "process.exit(1);",
            "pyproject.toml": "[build-system]\nrequires=['malicious']",
            "ruff.toml": "extend = 'malicious'",
            "package.json": '{"scripts": {"postinstall": "malicious"}}',
            "src/safe_module.py": "x = 1\n",
        }

        # Intercept written files to verify malicious configs are skipped
        written_paths = []
        orig_write = Path.write_text

        def mock_write(self, data, *args, **kwargs):
            written_paths.append(self.name)
            return orig_write(self, data, *args, **kwargs)

        with patch.object(Path, "write_text", side_effect=mock_write, autospec=True):
            await runner.run(malicious_files, contents)

        # Config files must never have been written
        for config_name in REPO_CONFIG_FILES:
            assert config_name not in written_paths
        assert "safe_module.py" in written_paths
