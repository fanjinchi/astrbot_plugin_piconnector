# Agent Notes for astrbot_plugin_piconnector

This is an [AstrBot](https://github.com/AstrBotDevs/AstrBot) plugin. It is **not** a standalone application; AstrBot loads it at runtime.

## Plugin identity

- `metadata.yaml` is the source of truth for plugin metadata. `name` must start with `astrbot_plugin_`.
- The values in `metadata.yaml` and `README.md` are currently the hello-world template defaults. Update them when modifying the plugin.
- `main.py` is the entrypoint. The class registered with `@register("...", author, desc, version)` is what AstrBot instantiates.

## Architecture

- Extend `astrbot.api.star.Star` and implement `initialize()` / `terminate()` if you need lifecycle hooks.
- Commands are declared with `@filter.command("<cmd>")`; users trigger them with `/<cmd>`.
- Use `event.plain_result(...)` to reply and `yield` to stream results.
- Import only from the AstrBot public API (`astrbot.api.*`). Do not import AstrBot internals.

## Verification

- There is no test suite, lint config, or CI in this repo yet.
- The only practical way to verify changes is to load the plugin in AstrBot and run the command.
- Plugin docs: https://docs.astrbot.app/dev/star/plugin-new.html (Chinese) / https://docs.astrbot.app/en/dev/star/plugin-new.html (English)
