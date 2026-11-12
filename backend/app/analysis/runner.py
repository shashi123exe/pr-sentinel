"""Orchestrator for isolated static analysis execution (Ruff, Bandit, ESLint)."""

import asyncio
import logging
import os
import tempfile
from pathlib import Path, PurePosixPath

from app.analysis.bandit_runner import build_bandit_command, parse_bandit_json
from app.analysis.eslint_runner import (
    build_eslint_command,
    get_eslint_paths,
    parse_eslint_json,
)
from app.analysis.ruff_runner import build_ruff_command, parse_ruff_json
from app.schemas.analysis import StaticAnalysisResult, StaticFinding
from app.schemas.diff import ProcessedFile

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT_SECONDS = 30.0
MAX_OUTPUT_BYTES = 5_000_000

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

# Configuration files from reviewed repositories that must be excluded
# from the analysis workspace
REPO_CONFIG_FILES = {
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.json",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    "ruff.toml",
    ".ruff.toml",
    "pyproject.toml",
    ".bandit",
    "bandit.yaml",
    "package.json",
    "setup.py",
    "setup.cfg",
}


def get_safe_environment(temp_dir: Path) -> dict[str, str]:
    """Build a minimal safe environment dict without leaking application secrets."""
    safe_keys = [
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "APPDATA",
    ]
    env = {k: os.environ[k] for k in safe_keys if k in os.environ}
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    return env


async def run_subprocess_tool(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    tool_name: str,
) -> tuple[str, str | None]:
    """Execute a static analysis subprocess with timeout and capped output.

    Returns:
        Tuple of (stdout_text, error_message_or_None).
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        # Cap output size safely
        stdout_str = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr_str = stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        # Linters often exit with non-zero when findings are detected
        # Check if output is valid JSON even if returncode != 0
        if proc.returncode != 0 and not stdout_str.strip().startswith(("{", "[")):
            logger.warning("%s exited %d: %s", tool_name, proc.returncode, stderr_str)
            err_msg = (
                f"{tool_name} error (exit {proc.returncode}): "
                f"{stderr_str.strip()[:200]}"
            )
            return (stdout_str, err_msg)

        return stdout_str, None

    except TimeoutError:
        logger.error("%s timed out after %.1fs", tool_name, SUBPROCESS_TIMEOUT_SECONDS)
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, Exception):
                pass
        return "", f"{tool_name} timed out after {SUBPROCESS_TIMEOUT_SECONDS}s"

    except Exception as exc:
        logger.error("Failed to execute %s: %s", tool_name, exc)
        return "", f"{tool_name} execution failed: {exc}"


class StaticAnalysisRunner:
    """Manages creation of safe temporary workspaces and static analysis dispatch."""

    async def run(
        self,
        files: list[ProcessedFile],
        file_contents: dict[str, str],
    ) -> StaticAnalysisResult:
        """Run eligible static analysis tools on temporary HEAD file copies.

        Security Guarantees:
        - Runs only inside an isolated temporary directory.
        - Verifies paths against directory traversal.
        - Uses argument lists without shell=True.
        - Does not pass credentials or tokens to subprocesses.
        - Guarantees complete cleanup of temporary directories.
        """
        all_findings: list[StaticFinding] = []
        tool_errors: list[str] = []
        tools_run: list[str] = []

        # Identify eligible code files that have content
        eligible = [
            f
            for f in files
            if f.skipped_reason is None
            and f.path in file_contents
            and file_contents[f.path]
        ]
        if not eligible:
            return StaticAnalysisResult(findings=[], tool_errors=[], tools_run=[])

        added_lines_by_file = {f.path: f.added_line_numbers for f in eligible}
        has_python = any(f.language == "python" for f in eligible)
        has_js_ts = any(
            f.language in {"javascript", "typescript", "tsx", "jsx"} for f in eligible
        )

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str).resolve()
            safe_env = get_safe_environment(temp_dir)

            # 1. Write file contents into temp_dir with strict path traversal validation
            for proc_file in eligible:
                raw_path = proc_file.path
                clean_path = raw_path.replace("\\", "/").strip()

                # Exclude repository configuration files from analysis workspace
                if Path(clean_path).name.lower() in REPO_CONFIG_FILES:
                    logger.info(
                        "Excluded repository config file from workspace: %s",
                        raw_path,
                    )
                    continue

                # Reject UNC paths or alternate data streams
                if (
                    clean_path.startswith("//")
                    or raw_path.startswith(("\\\\", "//"))
                    or ":" in clean_path
                ):
                    logger.warning("Rejected UNC or stream path: %s", raw_path)
                    continue

                if (
                    PurePosixPath(clean_path).is_absolute()
                    or Path(clean_path).is_absolute()
                    or Path(raw_path).is_absolute()
                    or bool(Path(raw_path).drive)
                ):
                    logger.warning("Rejected absolute or drive path: %s", raw_path)
                    continue

                rel_parts = PurePosixPath(clean_path).parts
                if any(part in {"..", "", "."} for part in rel_parts):
                    logger.warning("Rejected traversal path: %s", raw_path)
                    continue

                # Reject Windows reserved device names
                if any(
                    PurePosixPath(part).stem.upper() in WINDOWS_RESERVED_NAMES
                    for part in rel_parts
                ):
                    logger.warning(
                        "Rejected Windows reserved device name in path: %s", raw_path
                    )
                    continue

                target_file = (temp_dir / clean_path).resolve()
                try:
                    if (
                        not target_file.is_relative_to(temp_dir)
                        or target_file == temp_dir
                    ):
                        logger.warning("Path escaped tempdir: %s", raw_path)
                        continue
                except ValueError:
                    logger.warning("Path escaped tempdir: %s", raw_path)
                    continue

                # Ensure path or parent directories are not symlinks escaping workspace
                if target_file.is_symlink() or any(
                    p.is_symlink()
                    for p in target_file.parents
                    if p != temp_dir and p.is_relative_to(temp_dir)
                ):
                    logger.warning(
                        "Rejected symlink path escaping tempdir: %s", raw_path
                    )
                    continue

                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(file_contents[raw_path], encoding="utf-8")

            # 2. Run Ruff if Python files exist
            if has_python:
                tools_run.append("ruff")
                ruff_cmd = build_ruff_command(temp_dir)
                ruff_out, ruff_err = await run_subprocess_tool(
                    ruff_cmd, temp_dir, safe_env, "ruff"
                )
                if ruff_err:
                    tool_errors.append(ruff_err)
                if ruff_out:
                    all_findings.extend(
                        parse_ruff_json(
                            ruff_out, temp_dir, file_contents, added_lines_by_file
                        )
                    )

            # 3. Run Bandit if Python files exist
            if has_python:
                tools_run.append("bandit")
                bandit_cmd = build_bandit_command(temp_dir)
                bandit_out, bandit_err = await run_subprocess_tool(
                    bandit_cmd, temp_dir, safe_env, "bandit"
                )
                if bandit_err:
                    tool_errors.append(bandit_err)
                if bandit_out:
                    all_findings.extend(
                        parse_bandit_json(
                            bandit_out, temp_dir, file_contents, added_lines_by_file
                        )
                    )

            # 4. Run ESLint if JS/TS files exist
            if has_js_ts:
                node_exe, eslint_bin, eslint_config = get_eslint_paths()
                if not node_exe:
                    tool_errors.append(
                        "eslint skipped: Node.js executable not found in PATH"
                    )
                elif not eslint_bin.exists():
                    tool_errors.append(
                        f"eslint skipped: ESLint binary not found at {eslint_bin}"
                    )
                else:
                    tools_run.append("eslint")
                    eslint_cmd = build_eslint_command(
                        node_exe, eslint_bin, eslint_config, temp_dir
                    )
                    eslint_out, eslint_err = await run_subprocess_tool(
                        eslint_cmd, temp_dir, safe_env, "eslint"
                    )
                    if eslint_err:
                        tool_errors.append(eslint_err)
                    if eslint_out:
                        all_findings.extend(
                            parse_eslint_json(
                                eslint_out, temp_dir, file_contents, added_lines_by_file
                            )
                        )

        return StaticAnalysisResult(
            findings=all_findings,
            tool_errors=tool_errors,
            tools_run=tools_run,
        )
