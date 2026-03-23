"""Slack conversation manager -- maps threads to Claude sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from researchloop.db.database import Database
    from researchloop.sprints.manager import SprintManager

logger = logging.getLogger(__name__)

_ACTION_RE = re.compile(r"\[ACTION:\s*(\w+)\s*(\{.*?\})\]", re.DOTALL)


def _md_to_slack(text: str) -> str:
    """Convert markdown formatting to Slack mrkdwn."""
    lines = text.split("\n")
    result = []
    for line in lines:
        line = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", line)
        result.append(line)
    return "\n".join(result)


class ConversationManager:
    """Maps Slack threads to Claude CLI sessions.

    Stores message history locally so we don't need to
    query Slack's API for thread context.
    """

    def __init__(
        self,
        db: Database,
        sprint_manager: SprintManager | None = None,
    ) -> None:
        self.db = db
        self.sprint_manager = sprint_manager

    # ----------------------------------------------------------
    # Session CRUD
    # ----------------------------------------------------------

    async def get_session(self, thread_ts: str) -> dict | None:
        return await self.db.fetch_one(
            "SELECT * FROM slack_sessions WHERE thread_ts = ?",
            (thread_ts,),
        )

    async def create_session(
        self,
        thread_ts: str,
        study_name: str | None = None,
        sprint_id: str | None = None,
        session_id: str | None = None,
        messages: list[dict] | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO slack_sessions"
            " (thread_ts, sprint_id, session_id,"
            " study_name, messages_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                thread_ts,
                sprint_id,
                session_id,
                study_name,
                json.dumps(messages or []),
            ),
        )

    async def update_session_id(self, thread_ts: str, session_id: str) -> None:
        await self.db.execute(
            "UPDATE slack_sessions SET session_id = ? WHERE thread_ts = ?",
            (session_id, thread_ts),
        )

    async def _append_message(
        self,
        thread_ts: str,
        role: str,
        text: str,
    ) -> None:
        """Append a message to the thread's stored history."""
        session = await self.get_session(thread_ts)
        if session is None:
            return
        msgs = []
        raw = session.get("messages_json")
        if raw:
            try:
                msgs = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        msgs.append({"role": role, "text": text})
        await self.db.execute(
            "UPDATE slack_sessions SET messages_json = ? WHERE thread_ts = ?",
            (json.dumps(msgs), thread_ts),
        )

    async def store_bot_message(self, thread_ts: str, text: str) -> None:
        """Store a bot message (e.g. notification) for a thread."""
        session = await self.get_session(thread_ts)
        if session is None:
            # Create a session for this thread.
            # Extract sprint ID from the text if present.
            sid = None
            match = re.search(r"sp-[0-9a-f]{6}", text)
            if match:
                sid = match.group(0)
            await self.create_session(
                thread_ts,
                sprint_id=sid,
                messages=[{"role": "bot", "text": text}],
            )
        else:
            await self._append_message(thread_ts, "bot", text)

    # ----------------------------------------------------------
    # Context building
    # ----------------------------------------------------------

    async def _build_context(self) -> str:
        parts = [
            "You are the ResearchLoop assistant, helping "
            "researchers plan and manage automated research "
            "sprints on HPC clusters.",
            "",
            "IMPORTANT: Format responses for Slack. Use *bold* "
            "(single asterisks), _italic_, `code`, and bullet "
            "points with •. Do NOT use markdown headers (##), "
            "**double asterisks**, or [links](url).",
            "",
            "You can:",
            "- Discuss research ideas and help plan sprints",
            "- Review results from completed sprints",
            "- Suggest what to investigate next",
            "- Look up papers and references (web access)",
            "- Execute actions via [ACTION: ...] tags",
            "",
            "## Available Actions",
            '[ACTION: sprint_run {"study": "name", "idea": "..."}]',
            '[ACTION: sprint_list {"study": "name"}]',
            '[ACTION: sprint_show {"id": "sp-abc123"}]',
            '[ACTION: sprint_cancel {"id": "sp-abc123"}]',
            '[ACTION: study_show {"name": "study-name"}]',
            '[ACTION: loop_start {"study": "name", "count": 5, '
            '"context": "optional guidance"}]',
            "",
            "Only include an action when the user clearly wants "
            "to execute it. Always explain what you're doing.",
            "",
        ]

        studies = await self.db.fetch_all(
            "SELECT name, cluster, description FROM studies"
        )
        if studies:
            parts.append("## Available Studies")
            for s in studies:
                desc = s.get("description") or ""
                parts.append(f"- *{s['name']}*: {desc}")
            parts.append("")

        sprints = await self.db.fetch_all(
            "SELECT id, study_name, idea, status, summary "
            "FROM sprints ORDER BY created_at DESC LIMIT 10"
        )
        if sprints:
            parts.append("## Recent Sprints")
            for sp in sprints:
                idea = (sp.get("idea") or "")[:80]
                summary = (sp.get("summary") or "")[:100]
                parts.append(f"- {sp['id']} [{sp['status']}] {idea}")
                if summary:
                    parts.append(f"  Summary: {summary}")
            parts.append("")

        return "\n".join(parts)

    async def _sprint_context(self, sprint_id: str) -> str:
        sp = await self.db.fetch_one(
            "SELECT * FROM sprints WHERE id = ?",
            (sprint_id,),
        )
        if not sp:
            return ""
        parts = [
            f"## This Thread is About Sprint {sprint_id}",
            f"*Study:* {sp['study_name']}",
            f"*Status:* {sp['status']}",
            f"*Idea:* {sp.get('idea') or 'N/A'}",
        ]
        if sp.get("summary"):
            parts.append(f"*Summary:* {sp['summary']}")
        parts.append(
            "When the user says 'same prompt', 'this sprint',"
            " or 'again', they mean this one."
        )
        return "\n".join(parts)

    # ----------------------------------------------------------
    # Message handling
    # ----------------------------------------------------------

    async def handle_message(
        self,
        thread_ts: str,
        user_text: str,
        study_name: str | None = None,
        sprint_id: str | None = None,
        channel: str | None = None,
        bot_token: str | None = None,
    ) -> str:
        """Handle a conversational message from Slack."""
        session = await self.get_session(thread_ts)
        resume_id = session["session_id"] if session else None

        prompt = user_text
        if session is None:
            context = await self._build_context()

            # Auto-detect sprint ID from text.
            if not sprint_id:
                match = re.search(r"sp-[0-9a-f]{6}", user_text)
                if match:
                    sprint_id = match.group(0)

            if sprint_id:
                extra = await self._sprint_context(sprint_id)
                if extra:
                    context += f"\n\n{extra}"

            prompt = f"{context}\n\nUser: {user_text}"
        else:
            # Existing session — check stored sprint context.
            if not sprint_id:
                sprint_id = session.get("sprint_id")

            # Include stored thread history in the prompt
            # if this is the first Claude call (no resume_id).
            if not resume_id:
                raw = session.get("messages_json")
                if raw:
                    try:
                        msgs = json.loads(raw)
                        if msgs:
                            history = "\n".join(
                                f"{m['role'].title()}: {m['text'][:500]}" for m in msgs
                            )
                            context = await self._build_context()
                            if sprint_id:
                                extra = await self._sprint_context(sprint_id)
                                if extra:
                                    context += f"\n\n{extra}"
                            context += "\n\n## Prior Messages\n" + history
                            prompt = f"{context}\n\nUser: {user_text}"
                    except (json.JSONDecodeError, TypeError):
                        pass

        # Send a "thinking" indicator so the user knows the bot is working.
        if bot_token and channel:
            from researchloop.comms.slack import SlackNotifier

            _thinking_notifier = SlackNotifier(bot_token=bot_token, channel_id=channel)
            await _thinking_notifier._post_message("_Thinking..._", thread_ts=thread_ts)

        # Run Claude with restricted tools — web only.
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--allowedTools",
            "WebFetch",
            "WebSearch",
        ]
        if resume_id:
            cmd.extend(["--resume", resume_id])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            return "Sorry, the request timed out."
        except FileNotFoundError:
            return "Claude CLI is not available."

        if proc.returncode != 0:
            logger.error(
                "Claude CLI failed: %s",
                stderr.decode()[:500],
            )
            return "Sorry, something went wrong."

        # Parse response.
        raw = stdout.decode("utf-8", errors="replace").strip()
        response_text = raw
        new_session_id = None
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                response_text = (
                    data.get("result", "")
                    or data.get("text", "")
                    or data.get("content", "")
                    or raw
                )
                new_session_id = data.get("session_id")
        except json.JSONDecodeError:
            pass

        # Persist session.
        if session is None:
            await self.create_session(
                thread_ts,
                study_name=study_name,
                sprint_id=sprint_id,
                session_id=new_session_id,
                messages=[
                    {"role": "user", "text": user_text},
                    {"role": "bot", "text": response_text[:500]},
                ],
            )
        else:
            if new_session_id:
                await self.update_session_id(thread_ts, new_session_id)
            await self._append_message(thread_ts, "user", user_text)
            await self._append_message(thread_ts, "bot", response_text[:500])

        # Execute any actions Claude requested.
        action_results = await self._execute_actions(response_text)
        if action_results:
            response_text = _ACTION_RE.sub("", response_text).strip()
            response_text += "\n\n" + "\n".join(action_results)

        return _md_to_slack(response_text)

    # ----------------------------------------------------------
    # Action execution
    # ----------------------------------------------------------

    async def _execute_actions(self, text: str) -> list[str]:
        results: list[str] = []
        for match in _ACTION_RE.finditer(text):
            action = match.group(1)
            try:
                params: dict[str, Any] = json.loads(match.group(2))
            except json.JSONDecodeError:
                results.append(f":warning: Failed to parse: {action}")
                continue
            result = await self._run_action(action, params)
            results.append(result)
        return results

    async def _run_action(self, action: str, params: dict[str, Any]) -> str:
        if self.sprint_manager is None:
            return ":warning: Sprint manager not available."

        try:
            if action == "sprint_run":
                study = params.get("study", "")
                idea = params.get("idea", "")
                if not study or not idea:
                    return ":warning: sprint_run needs 'study' and 'idea'"
                sprint = await self.sprint_manager.run_sprint(study, idea)
                return f":rocket: Sprint *{sprint.id}* submitted for *{study}*"

            if action == "sprint_list":
                study = params.get("study")
                sprints = await self.sprint_manager.list_sprints(
                    study_name=study, limit=10
                )
                if not sprints:
                    return "No sprints found."
                lines = [
                    f"• *{s['id']}* [{s['status']}] {(s.get('idea') or '')[:50]}"
                    for s in sprints
                ]
                return "Sprints:\n" + "\n".join(lines)

            if action == "sprint_show":
                sid = params.get("id", "")
                if not sid:
                    return ":warning: sprint_show needs 'id'"
                sp = await self.sprint_manager.get_sprint(sid)
                if not sp:
                    return f":warning: Sprint {sid} not found"
                idea = (sp.get("idea") or "")[:100]
                summary = sp.get("summary") or ""
                return (
                    f"*{sp['id']}* [{sp['status']}]\n"
                    f"*Study:* {sp['study_name']}\n"
                    f"*Idea:* {idea}\n"
                    f"*Created:* {sp['created_at']}\n"
                    + (f"*Summary:* {summary}" if summary else "")
                )

            if action == "sprint_cancel":
                sid = params.get("id", "")
                if not sid:
                    return ":warning: sprint_cancel needs 'id'"
                ok = await self.sprint_manager.cancel_sprint(sid)
                return (
                    f":octagonal_sign: Sprint {sid} cancelled"
                    if ok
                    else f":warning: Failed to cancel {sid}"
                )

            if action == "study_show":
                from researchloop.db import queries

                name = params.get("name", "")
                if not name:
                    return ":warning: study_show needs 'name'"
                study = await queries.get_study(self.db, name)
                if not study:
                    return f":warning: Study {name} not found"
                sprints = await queries.list_sprints(self.db, study_name=name, limit=5)
                lines = [
                    f"*{study['name']}*\n"
                    f"*Cluster:* {study['cluster']}\n"
                    f"*Description:* "
                    f"{study.get('description', '')}\n"
                ]
                if sprints:
                    lines.append("*Recent sprints:*")
                    for s in sprints:
                        lines.append(
                            f"  • {s['id']} [{s['status']}]"
                            f" {(s.get('idea') or '')[:40]}"
                        )
                return "\n".join(lines)

            if action == "loop_start":
                from researchloop.sprints.auto_loop import (
                    AutoLoopController,
                )

                study = params.get("study", "")
                count = params.get("count", 5)
                ctx = params.get("context", "")
                if not study:
                    return ":warning: loop_start needs 'study'"
                ctrl = AutoLoopController(
                    db=self.sprint_manager.db,
                    sprint_manager=self.sprint_manager,
                    config=self.sprint_manager.config,
                )
                loop_id = await ctrl.start(study, count, context=ctx)
                return (
                    f":repeat: Auto-loop *{loop_id}* "
                    f"started for *{study}* ({count} sprints)"
                )

            return f":warning: Unknown action: {action}"

        except Exception as exc:
            logger.exception("Action %s failed", action)
            return f":x: Action failed: {exc}"
