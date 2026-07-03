"""Manage per-chat pi RPC connections and session lifecycle."""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from astrbot.api import logger

from .connection import PiConnection, PiError
from .models import SessionInfo


class PiConnectionManager:
    """Owns one PiConnection per AstrBot chat context."""

    def __init__(self, session_dir: Optional[str] = None, executable: str = "pi"):
        self.session_dir = session_dir
        self.executable = executable
        self._connections: Dict[str, PiConnection] = {}

    def _resolve_session_dir(self) -> str:
        """Return the absolute path to the pi session storage directory."""
        if self.session_dir:
            return os.path.expanduser(self.session_dir)
        return os.path.expanduser("~/.pi/agent/sessions")

    def _session_key(self, event) -> str:
        """Build a unique key for the chat context behind the event."""
        platform = event.get_platform_name() if hasattr(event, "get_platform_name") else "unknown"
        group_id = event.get_group_id() if hasattr(event, "get_group_id") else ""
        sender_id = event.get_sender_id() if hasattr(event, "get_sender_id") else ""
        return f"{platform}:{group_id}:{sender_id}"

    def _session_dir_for_cwd(self, cwd: str) -> str:
        """Return the pi directory name that encodes a cwd."""
        encoded = cwd.replace("/", "-")
        return f"--{encoded}--"

    def _extract_session_id(self, filename: str) -> str:
        """Extract the session UUID from a pi session filename."""
        stem = Path(filename).stem
        if "_" in stem:
            return stem.split("_", 1)[1]
        return stem

    def _find_session_file(self, session_id_or_path: str) -> Optional[str]:
        """Resolve a session id or partial id to an absolute session file path."""
        expanded = os.path.expanduser(session_id_or_path)
        if os.path.isabs(expanded) and os.path.isfile(expanded):
            return expanded

        session_root = self._resolve_session_dir()
        if not os.path.isdir(session_root):
            return None

        candidates = []
        for root, _dirs, files in os.walk(session_root):
            for f in files:
                if f.endswith(".jsonl"):
                    full = os.path.join(root, f)
                    sid = self._extract_session_id(f)
                    if sid == session_id_or_path or f.startswith(session_id_or_path):
                        return full
                    if session_id_or_path in sid or session_id_or_path in f:
                        candidates.append((full, sid))

        # Prefer the best partial match: the one whose session id starts with the query.
        for full, sid in candidates:
            if sid.startswith(session_id_or_path):
                return full

        # Fall back to the first candidate if any.
        if candidates:
            return candidates[0][0]

        return None

    async def get_connection(self, event, *, create: bool = True) -> Optional[PiConnection]:
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
        name: Optional[str] = None,
    ) -> SessionInfo:
        """Open a new pi session at an absolute directory path."""
        if not os.path.isabs(path):
            raise PiError(f"Session path must be absolute: {path}")
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
        session_id_or_path: str,
    ) -> SessionInfo:
        """Resume an existing pi session by id or file path."""
        session_file = self._find_session_file(session_id_or_path)
        if not session_file:
            raise PiError(f"Session not found: {session_id_or_path}")

        header = self._read_session_header(session_file)
        if not header:
            raise PiError(f"Session file is corrupted or empty: {session_file}")

        key = self._session_key(event)
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

    def list_sessions(self, directory: Optional[str] = None) -> List[SessionInfo]:
        """List pi sessions in a directory or across all stored sessions."""
        session_root = self._resolve_session_dir()
        if not os.path.isdir(session_root):
            return []

        sessions: List[SessionInfo] = []
        if directory:
            if not os.path.isabs(directory):
                raise PiError(f"Directory must be absolute: {directory}")
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

    def _read_session_dir(self, path: str) -> List[SessionInfo]:
        """Read all session headers from a pi session storage directory."""
        sessions = []
        for f in os.listdir(path):
            if f.endswith(".jsonl"):
                full = os.path.join(path, f)
                info = self._read_session_header(full)
                if info:
                    sessions.append(info)
        return sessions

    def _read_session_header(self, session_file: str) -> Optional[SessionInfo]:
        """Read the header line of a pi session JSONL file."""
        try:
            with open(session_file, "r", encoding="utf-8") as f:
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
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read session header %s: %s", session_file, exc)
            return None

    def _format_session_info(self, state: Dict, conn: PiConnection) -> SessionInfo:
        """Build a SessionInfo from pi get_state and connection metadata."""
        return SessionInfo(
            session_id=state.get("sessionId", ""),
            session_file=state.get("sessionFile", ""),
            cwd=conn.cwd or "",
            session_name=state.get("sessionName"),
            message_count=state.get("messageCount", 0),
            thinking_level=state.get("thinkingLevel"),
            is_streaming=state.get("isStreaming", False),
        )

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
