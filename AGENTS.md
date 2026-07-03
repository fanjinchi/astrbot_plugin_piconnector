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

- Run tests: `uv run pytest tests/ -v` (from the plugin root or the AstrBot root).
- Run lint: `uv run ruff check main.py tests/`
- The plugin can also be loaded in AstrBot to verify commands end-to-end.
- Plugin docs: https://docs.astrbot.app/dev/star/plugin-new.html (Chinese) / https://docs.astrbot.app/en/dev/star/plugin-new.html (English)

## Testing conventions

- Tests live under `tests/` and are executed with `pytest`.
- Write tests in **pytest** style (top-level functions or plain classes, plain `assert` statements).
- Use `@pytest.mark.asyncio` for async tests and `@pytest.fixture` for setup/teardown.
- Use `pytest.raises(...)` instead of `with self.assertRaises(...)`.
- Prefer `pytest.mark.parametrize` or inline `self.subTest`-like loops for similar cases.
- Use `unittest.mock.MagicMock` for synchronous mocks and `unittest.mock.AsyncMock` for async methods/dependencies.
- Every test file must import the shared setup before importing `pi_connector` or `main`:

  ```python
  # isort: off
  import _helpers  # noqa: F401
  from pi_connector import ...  # noqa: E402
  # isort: on
  ```

- `_helpers.py` ensures the project root is on `sys.path` and mocks the AstrBot logger so tests can run without the full AstrBot runtime.
- `tests/conftest.py` provides shared pytest fixtures and mocks the AstrBot public API modules that `main.py` depends on. Tests importing `main` should rely on the fixtures exported from `conftest.py` rather than duplicating inline mocks.
- `main.py` directly imports AstrBot public API modules (`astrbot.api.event`, `astrbot.api.star`, `astrbot.core.utils.astrbot_path`). Tests that import `main` receive the needed fakes through `conftest.py`.

