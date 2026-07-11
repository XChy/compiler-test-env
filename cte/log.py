"""Tiny logging / process helpers shared by the builders."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_USE_COLOR = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def info(msg: str) -> None:
    print(_c("1;34", "==>") + " " + msg, file=sys.stderr)


def step(msg: str) -> None:
    print(_c("1;32", "  ->") + " " + msg, file=sys.stderr)


def warn(msg: str) -> None:
    print(_c("1;33", "warning:") + " " + msg, file=sys.stderr)


def error(msg: str) -> None:
    print(_c("1;31", "error:") + " " + msg, file=sys.stderr)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    """Run a command, streaming output, raising on failure."""
    step("$ " + " ".join(str(c) for c in cmd))
    full_env = {**os.environ, **env} if env else None
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=full_env, check=True)


def run_to_log(
    cmd: list[str], log_path: Path, cwd: Path | None = None, env: dict | None = None
) -> None:
    """Run a noisy command, retaining full output in ``log_path``.

    Build systems such as Ninja print one line per object file. Keep that noise
    out of the terminal while preserving it for diagnosis; surface the final
    lines if the command fails.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    step("$ " + " ".join(str(c) for c in cmd) + f"  (log: {log_path})")
    full_env = {**os.environ, **env} if env else None
    with log_path.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            [str(c) for c in cmd], cwd=cwd, env=full_env,
            stdout=output, stderr=subprocess.STDOUT,
        )
    if result.returncode:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
        error(f"command failed; last output from {log_path}:")
        print("\n".join(tail), file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, [str(c) for c in cmd])


def run_retry(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    attempts: int = 3,
    on_retry=None,
) -> None:
    """Run a command, retrying with backoff on failure.

    ``on_retry`` is an optional callback invoked before each retry (e.g. to
    clean up a partial checkout). Useful for flaky network operations.
    """
    for attempt in range(1, attempts + 1):
        try:
            run(cmd, cwd=cwd, env=env)
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            delay = 5 * attempt
            warn(f"command failed (attempt {attempt}/{attempts}); "
                 f"retrying in {delay}s")
            if on_retry is not None:
                on_retry()
            time.sleep(delay)


def capture(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(
        [str(c) for c in cmd], cwd=cwd, check=True,
        stdout=subprocess.PIPE, text=True,
    ).stdout.strip()


def require(*tools: str) -> None:
    """Fail early if a required host tool is missing."""
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise RuntimeError(
            f"missing required host tool(s): {', '.join(missing)}. "
            "Please install them and retry."
        )
