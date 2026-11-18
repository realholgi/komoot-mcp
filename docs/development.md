# Development

[Project overview](../README.md)

## Set up the environment

Use Python 3.11 or later. Run these commands from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
komoot-mcp --help
```

The integration uses `kompy` internals, so the dependency stays below `0.1.0`.

## Run tests

```bash
python -m pytest tests
```

Tests use local fixtures and mocked remote requests.
The suite blocks unmocked HTTP requests.
They do not verify current live Komoot behavior.
