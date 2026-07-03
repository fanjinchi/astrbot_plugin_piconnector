"""Demo script showing the formatted output of format_commands_list."""

import sys
import types
from pathlib import Path

# Ensure the project root is on sys.path so pi_connector can be imported.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# Mock AstrBot public API so pi_connector.commands can be imported standalone.
astrbot = types.ModuleType("astrbot")
astrbot_api = types.ModuleType("astrbot.api")
astrbot_api.logger = types.SimpleNamespace(
    info=print, debug=print, warning=print, error=print
)
astrbot.api = astrbot_api
sys.modules["astrbot"] = astrbot
sys.modules["astrbot.api"] = astrbot_api

from pi_connector.commands import format_commands_list  # noqa: E402


SAMPLE_COMMANDS = [
    {
        "name": "opsx-explore",
        "description": "Explore the codebase structure and identify relevant files.",
        "source": "pi",
    },
    {
        "name": "opsx-ask",
        "description": "Ask a question about the code and get an answer.",
        "source": "pi",
    },
    {
        "name": "opsx-plan",
        "description": "Create a plan for implementing a change.",
        "source": "pi",
    },
    {
        "name": "custom-cmd",
        "description": "",
        "source": "builtin",
    },
]


def main() -> None:
    output = format_commands_list(SAMPLE_COMMANDS)
    print("=" * 60)
    print("format_commands_list output")
    print("=" * 60)
    print(output)
    print("=" * 60)


if __name__ == "__main__":
    main()
