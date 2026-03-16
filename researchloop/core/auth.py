"""Claude CLI authentication helpers."""

from __future__ import annotations

import asyncio
import shutil
import subprocess


def check_claude_auth() -> tuple[bool, str]:
    """Check whether the Claude CLI is authenticated.

    Returns ``(ok, detail)`` where *ok* is True if authenticated
    and *detail* is a human-readable status string.
    """
    claude = shutil.which("claude")
    if claude is None:
        return False, "Claude CLI not found in PATH"

    try:
        result = subprocess.run(
            [claude, "-p", "say ok", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, "Claude CLI is working"
        stderr = result.stderr.strip()
        if "auth" in stderr.lower() or "login" in stderr.lower():
            return False, "Not logged in — run: researchloop login"
        return False, f"Claude CLI error: {stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "Claude CLI timed out"
    except Exception as exc:
        return False, f"Error checking auth: {exc}"


async def check_claude_auth_async() -> tuple[bool, str]:
    """Async version of :func:`check_claude_auth`."""
    claude = shutil.which("claude")
    if claude is None:
        return False, "Claude CLI not found in PATH"

    try:
        proc = await asyncio.create_subprocess_exec(
            claude,
            "-p",
            "say ok",
            "--output-format",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            return True, "Claude CLI is working"
        err = stderr.decode("utf-8", errors="replace").strip()
        if "auth" in err.lower() or "login" in err.lower():
            return False, "Not logged in"
        return False, f"Claude CLI error: {err[:200]}"
    except asyncio.TimeoutError:
        return False, "Claude CLI timed out"
    except Exception as exc:
        return False, f"Error checking auth: {exc}"
