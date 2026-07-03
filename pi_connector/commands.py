"""Formatting and parsing helpers for the pi connector plugin commands."""

from typing import List, Optional, Tuple

from .models import SessionInfo, UIRequest


def strip_command_prefix(message: str, prefix: str) -> str:
    """Remove the leading `/prefix` or `prefix` from a message string."""
    text = message.strip()
    for candidate in (f"/{prefix}", prefix):
        if text.startswith(candidate):
            return text[len(candidate):].strip()
    return text


def parse_subcommand(text: str) -> Tuple[str, str]:
    """Split the text after the command prefix into subcommand and the rest.

    Returns a tuple (subcommand, rest). If `text` is empty, both are empty.
    """
    parts = text.split(maxsplit=1)
    if not parts:
        return "", ""
    subcommand = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    return subcommand, rest


def format_session_info(info: SessionInfo) -> str:
    """Format a SessionInfo for display to the user."""
    lines = [
        f"Session: {info.session_id}",
        f"Name: {info.session_name or '(unnamed)'}",
        f"Working directory: {info.cwd}",
        f"File: {info.session_file}",
        f"Messages: {info.message_count}",
    ]
    if info.thinking_level:
        lines.append(f"Thinking level: {info.thinking_level}")
    return "\n".join(lines)


def format_session_list(sessions: List[SessionInfo]) -> str:
    """Format a list of sessions for display to the user."""
    if not sessions:
        return "No sessions found."

    lines = ["Available sessions:"]
    for idx, info in enumerate(sessions, start=1):
        name = info.session_name or "(unnamed)"
        lines.append(
            f"{idx}. {name}\n   ID: {info.session_id}\n   CWD: {info.cwd}\n   Messages: {info.message_count}"
        )
    return "\n".join(lines)


def format_ui_request(request: UIRequest) -> str:
    """Format a pi extension UI request for display to the user."""
    lines = [f"[Request #{request.local_id}]"]

    if request.title:
        lines.append(f"Title: {request.title}")
    if request.message:
        lines.append(f"Message: {request.message}")

    if request.method == "confirm":
        lines.append("Reply with: /pi confirm {id} yes  or  /pi confirm {id} no".format(id=request.local_id))
    elif request.method == "select":
        if request.options:
            lines.append("Options:")
            for idx, option in enumerate(request.options, start=1):
                lines.append(f"  {idx}. {option}")
        lines.append(
            "Reply with: /pi select {id} <option>  or  /pi select {id} <number>".format(
                id=request.local_id
            )
        )
    elif request.method == "input":
        lines.append("Reply with: /pi input {id} <value>".format(id=request.local_id))
    elif request.method == "editor":
        if request.prefill:
            lines.append(f"Prefill: {request.prefill}")
        lines.append("Reply with: /pi edit {id} <text>".format(id=request.local_id))
    else:
        lines.append("Reply with: /pi input {id} <value>".format(id=request.local_id))

    return "\n".join(lines)


def format_commands_list(commands: List[dict]) -> str:
    """Format the list of pi slash commands for display."""
    if not commands:
        return "No slash commands available."

    lines = ["Available pi commands:"]
    for cmd in commands:
        name = cmd.get("name", "unknown")
        description = cmd.get("description", "")
        source = cmd.get("source", "unknown")
        if description:
            lines.append(f"/{name} ({source}) - {description}")
        else:
            lines.append(f"/{name} ({source})")
    return "\n".join(lines)


def resolve_select_option(request: UIRequest, value: str) -> Optional[str]:
    """Resolve a user reply for a select request.

    Accepts either the option text or a 1-based index.
    Returns the option text if valid, otherwise None.
    """
    value = value.strip()
    if not request.options:
        return value

    # Try to match by exact text first.
    for option in request.options:
        if value == option:
            return option

    # Then try to interpret as a 1-based index.
    try:
        idx = int(value)
        if 1 <= idx <= len(request.options):
            return request.options[idx - 1]
    except ValueError:
        pass

    return None


def parse_ui_reply_args(rest: str) -> Tuple[Optional[int], str]:
    """Parse the local ID and value from a UI reply command.

    Returns (local_id, value). local_id is None if the ID is invalid.
    """
    parts = rest.split(maxsplit=1)
    if not parts:
        return None, ""
    try:
        local_id = int(parts[0])
    except ValueError:
        return None, rest

    value = parts[1] if len(parts) > 1 else ""
    return local_id, value
