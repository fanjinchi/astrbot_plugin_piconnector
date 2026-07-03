"""Manage per-chat pi RPC connections and session lifecycle."""

import json
import os
from pathlib import Path

from astrbot.api import logger

from .connection import PiConnection, PiError
from .models import SessionInfo


class PiConnectionManager:
    """Owns one PiConnection per AstrBot chat context."""

    def __init__(self, session_dir: str | None = None, executable: str = "pi"):
        self.session_dir = session_dir
        self.executable = executable
        self._connections: dict[str, PiConnection] = {}

    def _resolve_session_dir(self) -> str:
        """Return the absolute path to the pi session storage directory."""
        if self.session_dir:
            return os.path.expanduser(self.session_dir)
        return os.path.expanduser("~/.pi/agent/sessions")

    def _normalize_path(self, path: str) -> str:
        """Resolve a user-supplied path to an absolute path.

        Absolute paths are returned unchanged (with trailing separators
        normalized away). Paths starting with ``~`` are expanded to the user's
        home directory. Relative paths are resolved relative to the user's
        home directory, so ``code/`` becomes ``~/code/``.
        """
        expanded = os.path.expanduser(path)
        if os.path.isabs(expanded):
            return os.path.normpath(expanded)
        return os.path.normpath(os.path.join(os.path.expanduser("~"), expanded))

    def _session_key(self, event) -> str:
        """Build a unique key for the chat context behind the event."""
        platform = (
            event.get_platform_name()
            if hasattr(event, "get_platform_name")
            else "unknown"
        )
        group_id = event.get_group_id() if hasattr(event, "get_group_id") else ""
        sender_id = event.get_sender_id() if hasattr(event, "get_sender_id") else ""
        return f"{platform}:{group_id}:{sender_id}"

    def _session_dir_for_cwd(self, cwd: str) -> str:
        """Return the pi directory name that encodes a cwd.

        This mirrors pi's native layout: split the working directory by the
        platform path separator, drop empty parts (e.g. the leading slash on
        Unix), join them with ``-``, and wrap the result in ``--``. On Windows,
        the drive-letter colon is also encoded so the resulting name is valid.
        """
        cwd = os.path.normpath(cwd)
        parts = [part for part in cwd.split(os.sep) if part]
        encoded = "-".join(parts)
        if os.name == "nt":
            encoded = encoded.replace(":", "-")
        return f"--{encoded}--"

    def _extract_session_id(self, filename: str) -> str:
        """Extract the session UUID from a pi session filename."""
        stem = Path(filename).stem
        if "_" in stem:
            return stem.split("_", 1)[1]
        return stem

    def _find_session_file(self, session_id_or_path: str) -> str | None:
        """Resolve a session id or partial id to an absolute session file path.

        Matching order:
        1. If the input is an absolute path to an existing file, return it.
        2. Exact match on the session UUID.
        3. Unique prefix match on the session UUID.
        4. If multiple sessions match, raise PiError so the user can disambiguate.
        """
        expanded = os.path.expanduser(session_id_or_path)
        if os.path.isabs(expanded) and os.path.isfile(expanded):
            return expanded

        session_root = self._resolve_session_dir()
        if not os.path.isdir(session_root):
            return None

        all_files: list[str] = []
        exact_match: str | None = None
        prefix_matches: list[str] = []

        for root, _dirs, files in os.walk(session_root):
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                full = os.path.join(root, f)
                all_files.append(full)
                sid = self._extract_session_id(f)
                if sid == session_id_or_path:
                    exact_match = full
                elif sid.startswith(session_id_or_path):
                    prefix_matches.append(full)

        if exact_match:
            return exact_match

        if len(prefix_matches) == 1:
            return prefix_matches[0]

        if len(prefix_matches) > 1:
            lines = ["Multiple sessions match the query. Please use the full id:"]
            for full in prefix_matches:
                sid = self._extract_session_id(os.path.basename(full))
                lines.append(f"  {sid}  ({full})")
            raise PiError("\n".join(lines))

        return None

    def _find_most_recent_session(self) -> str | None:
        """Return the most recently modified session file in the session dir.

        Returns None if no session files exist.
        """
        session_root = self._resolve_session_dir()
        if not os.path.isdir(session_root):
            return None

        most_recent: str | None = None
        most_recent_mtime: float = 0.0
        for root, _dirs, files in os.walk(session_root):
            for f in files:
                if not f.endswith(".jsonl"):
                    continue
                full = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                if mtime > most_recent_mtime:
                    most_recent_mtime = mtime
                    most_recent = full

        return most_recent

    async def get_connection(
        self, event, *, create: bool = True
    ) -> PiConnection | None:
        """Return the existing connection for the chat or create a new one."""
        key = self._session_key(event)
        if key in self._connections:
            return self._connections[key]
        if not create:
            return None
        conn = PiConnection(session_dir=self.session_dir, executable=self.executable)
        self._connections[key] = conn
        return conn

    async def open_session(
        self,
        event,
        path: str,
        name: str | None = None,
    ) -> SessionInfo:
        """Open a new pi session at an absolute directory path."""
        path = self._normalize_path(path)
        if not os.path.isdir(path):
            raise PiError(f"Directory does not exist: {path}")

        key = self._session_key(event)
        await self.close_connection(event)

        conn = PiConnection(
            session_dir=self.session_dir,
            cwd=path,
            name=name,
            executable=self.executable,
        )
        self._connections[key] = conn
        await conn.start()

        result = await conn.new_session()
        if result.get("data", {}).get("cancelled"):
            raise PiError("Session creation was cancelled by an extension")

        state = await conn.get_state()
        return self._format_session_info(state, conn)

    async def resume_session(
        self,
        event,
        session_id_or_path: str | None = None,
    ) -> SessionInfo:
        """Resume an existing pi session by id, file path, or load the most recent.

        If ``session_id_or_path`` is empty or None, the most recently modified
        session file in the configured session directory is used.

        If the chat already has an active pi process, send ``switch_session`` to
        it instead of spawning a new process.
        """
        if not session_id_or_path:
            session_file = self._find_most_recent_session()
            if not session_file:
                raise PiError(
                    "No sessions found. Use /pi open to create a new session first."
                )
        else:
            session_file = self._find_session_file(session_id_or_path)
            if not session_file:
                raise PiError(f"Session not found: {session_id_or_path}")

        header = self._read_session_header(session_file)
        if not header:
            raise PiError(f"Session file is corrupted or empty: {session_file}")

        key = self._session_key(event)
        conn = await self.get_connection(event, create=False)

        if (
            conn is not None
            and conn.process is not None
            and conn.process.returncode is None
        ):
            # Reuse the existing RPC process and switch session files.
            await conn.switch_session(session_file)
            state = await conn.get_state()
            return self._format_session_info(state, conn)

        # No active process: close any stale connection and spawn a new one.
        await self.close_connection(event)

        conn = PiConnection(
            session_path=session_file,
            session_dir=self.session_dir,
            cwd=header.cwd,
            executable=self.executable,
        )
        self._connections[key] = conn
        await conn.start()

        state = await conn.get_state()
        return self._format_session_info(state, conn)

    def list_sessions(self, directory: str | None = None) -> list[SessionInfo]:
        """List pi sessions in a directory or across all stored sessions."""
        session_root = self._resolve_session_dir()
        if not os.path.isdir(session_root):
            return []

        sessions: list[SessionInfo] = []
        if directory:
            directory = self._normalize_path(directory)
            target_dir = self._session_dir_for_cwd(directory)
            target_path = os.path.join(session_root, target_dir)
            if os.path.isdir(target_path):
                sessions.extend(self._read_session_dir(target_path))
        else:
            for root, _dirs, files in os.walk(session_root):
                for f in files:
                    if f.endswith(".jsonl"):
                        full = os.path.join(root, f)
                        info = self._read_session_header(full)
                        if info:
                            sessions.append(info)

        return sessions

    def _read_session_dir(self, path: str) -> list[SessionInfo]:
        """Read all session headers from a pi session storage directory."""
        sessions = []
        for f in os.listdir(path):
            if f.endswith(".jsonl"):
                full = os.path.join(path, f)
                info = self._read_session_header(full)
                if info:
                    sessions.append(info)
        return sessions

    def _read_session_header(self, session_file: str) -> SessionInfo | None:
        """Read the header line of a pi session JSONL file."""
        try:
            with open(session_file, encoding="utf-8") as f:
                first_line = f.readline()
                if not first_line:
                    return None
                header = json.loads(first_line)
                if header.get("type") != "session":
                    return None
                return SessionInfo(
                    session_id=header.get("id", ""),
                    session_file=session_file,
                    cwd=header.get("cwd", ""),
                    session_name=header.get("name"),
                    message_count=header.get("messageCount", 0),
                    timestamp=header.get("timestamp"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read session header %s: %s", session_file, exc)
            return None

    def _format_session_info(self, state: dict, conn: PiConnection) -> SessionInfo:
        """Build a SessionInfo from pi get_state and connection metadata."""
        return SessionInfo(
            session_id=state.get("sessionId", ""),
            session_file=state.get("sessionFile", ""),
            cwd=conn.cwd or "",
            session_name=state.get("sessionName"),
            message_count=state.get("messageCount", 0),
            thinking_level=state.get("thinkingLevel"),
            is_streaming=state.get("isStreaming", False),
            timestamp=state.get("timestamp"),
        )

    async def get_active_cwd(self, event) -> str | None:
        """Return the working directory of the active session, or None."""
        conn = await self.get_connection(event, create=False)
        if not conn:
            return None
        return conn.cwd

    async def get_session_info(self, event) -> SessionInfo:
        """Return information about the active session for the chat."""
        conn = await self.get_connection(event, create=False)
        if not conn or not conn.process:
            raise PiError("No active pi session. Use /pi open or /pi resume first.")
        state = await conn.get_state()
        return self._format_session_info(state, conn)

    async def close_connection(self, event) -> None:
        """Close and remove the connection for the chat context."""
        key = self._session_key(event)
        conn = self._connections.pop(key, None)
        if conn:
            await conn.terminate()

    async def terminate_all(self) -> None:
        """Close all managed connections. Useful for plugin shutdown."""
        for conn in list(self._connections.values()):
            await conn.terminate()
        self._connections.clear()
