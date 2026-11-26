"""Minimal subprocess sandbox for educational Python execution.

Runs in a separate interpreter process with a restricted global namespace.
Platform notes:
- POSIX: applies ``resource`` rlimits (CPU, address space, file size, open
  files) plus a ``SIGALRM`` wall-clock alarm.
- Windows: ``resource`` and ``SIGALRM`` do not exist, so no rlimits are
  applied here. The parent process still enforces a hard wall-clock timeout
  via ``subprocess.run(..., timeout=...)`` and kills the child on expiry, so
  runaway code is still contained.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import sys

try:  # POSIX only
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore[assignment]

try:  # POSIX only
    import signal
except ImportError:  # pragma: no cover - signal exists on Windows, SIGALRM does not
    signal = None  # type: ignore[assignment]

HAS_POSIX_LIMITS = resource is not None and hasattr(signal, "SIGALRM") if signal else False

# Wall-clock budget enforced inside the child. The parent applies its own
# (longer) timeout as the outer backstop.
DEFAULT_WALL_CLOCK_SECONDS = 3


ALLOWED_MODULES = {
    "math", "random", "string", "collections", "itertools",
    "functools", "datetime", "json", "re", "typing",
    "statistics", "decimal", "fractions", "operator",
    "copy", "heapq", "bisect", "array",
}

BLOCKED_BUILTINS = {
    "open", "exec", "eval", "__import__", "compile",
    "globals", "locals", "breakpoint", "exit", "quit",
    "input", "help",
    # Introspection primitives — block sandbox escape via
    # getattr(().__class__, '__bases__')[0].__subclasses__() etc.
    "getattr", "setattr", "delattr", "vars", "dir",
    "type", "memoryview",
}

MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
MAX_FILE_SIZE_BYTES = 1024 * 1024
MAX_OPEN_FILES = 32
CPU_LIMIT_SECONDS = 2


def _apply_limits(wall_clock_seconds: int = DEFAULT_WALL_CLOCK_SECONDS) -> None:
    """Apply OS-level limits. No-op on Windows (parent enforces timeout)."""
    if not HAS_POSIX_LIMITS:
        return

    def _safe_set(limit_name: int, soft: int, hard: int) -> None:
        try:
            _, current_hard = resource.getrlimit(limit_name)
            target_hard = min(
                hard, current_hard if current_hard != resource.RLIM_INFINITY else hard
            )
            target_soft = min(soft, target_hard)
            resource.setrlimit(limit_name, (target_soft, target_hard))
        except (ValueError, OSError):
            return

    _safe_set(resource.RLIMIT_CPU, CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS)
    _safe_set(resource.RLIMIT_FSIZE, MAX_FILE_SIZE_BYTES, MAX_FILE_SIZE_BYTES)
    _safe_set(resource.RLIMIT_NOFILE, MAX_OPEN_FILES, MAX_OPEN_FILES)
    _safe_set(resource.RLIMIT_AS, MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES)
    signal.alarm(max(1, int(wall_clock_seconds)))


def _build_globals() -> dict:
    safe_builtins = {
        key: value
        for key, value in builtins.__dict__.items()
        if key not in BLOCKED_BUILTINS
    }
    safe_globals = {"__builtins__": safe_builtins}
    for module_name in ALLOWED_MODULES:
        try:
            safe_globals[module_name] = __import__(module_name)
        except (ImportError, ModuleNotFoundError):
            continue
    return safe_globals


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        code = str(payload.get("code", ""))
        wall_clock = payload.get("timeout_seconds") or DEFAULT_WALL_CLOCK_SECONDS
        _apply_limits(int(wall_clock))
        os.environ.clear()

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        safe_globals = _build_globals()

        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exec(code, safe_globals)  # noqa: S102

        sys.stdout.write(json.dumps({
            "success": True,
            "output": stdout_buf.getvalue()[:2000],
            "error": stderr_buf.getvalue()[:500],
        }))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({
            "success": False,
            "output": "",
            "error": f"{type(exc).__name__}: {exc}",
        }))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())