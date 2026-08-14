# Contributing

This project follows Home Assistant's own conventions where practical,
since it's built to sit alongside HA core's own integrations and to make
it easy for anyone familiar with HA development to contribute. Verified
against [Home Assistant's developer docs](https://developers.home-assistant.io/)
and [home-assistant/core](https://github.com/home-assistant/core), checked
2026-08-14.

## Code style

- [PEP8](https://peps.python.org/pep-0008/) and
  [PEP257](https://peps.python.org/pep-0257/) (docstring) compliance,
  enforced via [Ruff](https://docs.astral.sh/ruff/) - see `pyproject.toml`
  for the configured rule set (a trimmed-down version of HA core's own).
- Run `ruff format .` and `ruff check --fix .` before committing, or
  install the pre-commit hooks: `pre-commit install`.
- Comments are full sentences and end with a period.
- Imports are ordered (handled automatically by Ruff's isort rules).
- Prefer [f-strings](https://docs.python.org/3/reference/lexical_analysis.html#f-strings)
  over `.format()` or `%`-formatting - except in `_LOGGER` calls, which
  use `%`-style formatting so the string isn't built when the log level
  is suppressed:
  ```python
  _LOGGER.debug("Synced %d events from %s", count, entity_id)
  ```
- Log messages don't end with a period and don't repeat the
  component/integration name (HA adds that automatically). Never log
  API keys, tokens, or other credentials - even redacted/wrong ones.
  Use `_LOGGER.debug` for anything not meant for the end user;
  `_LOGGER.info` sparingly.
- Fully type-hint new code.
- For docstrings beyond a one-liner, use
  [Google-style](https://google.github.io/styleguide/pyguide.html#383-functions-and-methods)
  `Args:`/`Returns:`/`Raises:` sections, omitting types already covered
  by the type annotations.
- File headers: a one-line docstring describing what the file is about,
  e.g. `"""Sync coordinator for Calendar Mirror."""`.

## Commit messages

Straight from HA's own [Submit your work](https://developers.home-assistant.io/docs/development_submitting/)
guide:

- Write a meaningful commit message, not just `Update` or `Fix`.
- Start with a capital letter.
- Don't end with a period.
- Don't prefix with `[component]:` or `platform:`.
- Use the imperative voice: `Add source calendar picker`, not `Added` or
  `Adds source calendar picker`.

## Tests

Run `pytest` before submitting - see `tests/` for existing coverage
using `pytest-homeassistant-custom-component`, matching HA's own testing
conventions (mocked config entries, `hass`/`aioclient_mock` fixtures,
etc.) rather than a bespoke test setup.
