"""Claude CLI authentication helpers."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess


def check_claude_auth() -> tuple[bool, str]:
    """Check whether the Claude CLI is authenticated.

    Uses ``claude auth status`` which returns instant JSON.

    Returns ``(ok, detail)`` where *ok* is True if authenticated
    and *detail* is a human-readable status string.
    """
    claude = shutil.which("claude")
    if claude is None:
        return False, "Claude CLI not found in PATH"

    try:
        result = subprocess.run(
            [claude, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                if data.get("loggedIn"):
                    email = data.get("email", "")
                    sub = data.get("subscriptionType", "")
                    detail = f"{email} ({sub})" if email else "OK"
                    return True, detail
                return False, "Not logged in"
            except json.JSONDecodeError:
                return True, "Authenticated"
        return False, "Not logged in — run: researchloop login"
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
            "auth",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            try:
                data = json.loads(stdout.decode("utf-8"))
                if data.get("loggedIn"):
                    email = data.get("email", "")
                    sub = data.get("subscriptionType", "")
                    detail = f"{email} ({sub})" if email else "OK"
                    return True, detail
                return False, "Not logged in"
            except json.JSONDecodeError:
                return True, "Authenticated"
        return False, "Not logged in"
    except asyncio.TimeoutError:
        return False, "Claude CLI timed out"
    except Exception as exc:
        return False, f"Error checking auth: {exc}"
