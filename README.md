# Coldfront Common Utilities

utilities and helper functions for coldfront and coldfront plugins

## Optional extras

- `ad`: needed for `coldfront_utils.util.ad_search`. It installs coldfront's `ldap` extra, which
  includes python-ldap; python-ldap compiles against the OpenLDAP client libraries.
- `ssh`: fabric.

```
coldfront-utils[ad] @ git+https://github.com/Tufts-Technology-Services/coldfront-utils.git
```

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

The tests run against coldfront's own settings (`tests/settings.py`) with an in-memory SQLite
database. They live in `tests/` rather than inside the package because importing
`coldfront_utils` imports coldfront's models, which can't happen before Django's settings load.
