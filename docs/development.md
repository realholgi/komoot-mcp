# Development

[Project overview](../README.md) | [MCP setup](mcp-setup.md) | [Reference](reference.md)

## Set up the environment

Use Python 3.11 or later and `uv`. From the repository root:

```bash
uv sync --locked --extra dev
uv run --no-sync komoot-mcp --help
```

`uv sync` installs the project in editable mode.
`uv.lock` records the resolved development dependencies.
Use `--locked` to reject an outdated lock instead of silently replacing it.
To select the minimum supported Python explicitly, add `--python 3.11`.

## Module layout and seams

| Path | Responsibility |
| --- | --- |
| `src/komoot_mcp/server.py` | CLI, server construction, transport setup and registration |
| `src/komoot_mcp/protocol.py` | Argument handling, startup policy, annotations and result envelopes |
| `src/komoot_mcp/guidance.py` | Capability/reference resources and guidance prompts |
| `src/komoot_mcp/tools/` | Tool interfaces and adapters to domain operations |
| `src/komoot_mcp/context.py` | Request-local auth/client state and shared rate limiter/geocoder |
| `src/komoot_mcp/middleware.py` | Gateway authentication and per-request credentials |
| `src/komoot_mcp/auth.py` | Komoot login and session state |
| `src/komoot_mcp/client.py` | Komoot request and response handling |
| `src/komoot_mcp/collections.py` | Owned collection metadata, complete member replacement and read-back checks |
| `src/komoot_mcp/secure_http.py` | Direct HTTP transport policy |
| `src/komoot_mcp/routing.py` | Native planning, coordinate checks and save results |
| `src/komoot_mcp/gpx_import.py` | GPX import into route data |
| `src/komoot_mcp/overnight.py` | Sleeping modes, route snapshots and exact source cuts |
| `src/komoot_mcp/overnight_geodata.py` | Anonymous bounded OSM geometry requests |
| `src/komoot_mcp/overnight_places.py` | Mapped overnight places and source qualifiers |
| `src/komoot_mcp/bivouac.py` | Bounded terrain patches and mapped exclusions |
| `src/komoot_mcp/stage_planning.py` | Complete stage alternatives, access legs and inline GPX |
| `src/komoot_mcp/komoot_import.py` | Prepared-route account operations and read-back checks |
| `src/komoot_mcp/publication.py` | Preview, receipts, pending intents, locks and publication recovery |
| `src/komoot_mcp/artifacts.py` | Local artifacts and atomic export writes |
| `src/komoot_mcp/descriptions.py` | Optional client sampling for GPX descriptions |
| `tests/` | Synthetic fixtures and offline regression tests |

Keep tool adapters small. Put account transport behavior in the client, not in each tool.
Use the context helpers to resolve account state. Do not add process-global tenant credentials.
Keep publication records behind the publication interface.
Tests replace network behavior at client and HTTP seams without changing the tool interface.

## Dependency assumptions

The integration uses `kompy` internals, including `Tour._create_*`.
The declared dependency stays below `0.1.0`.
MCP stays below major version `2`.
The project also depends on `gpxpy`, `requests`, `Pillow`, `starlette` and `uvicorn`.
See `pyproject.toml` for supported ranges and `uv.lock` for exact resolved versions.

When changing dependency ranges, update the lock deliberately:

```bash
uv lock
uv sync --locked --extra dev
```

Review both files together. Do not update unrelated packages without a reason.

## Offline tests

After setup, run:

```bash
uv run --no-sync python -m pytest tests
```

Tests use synthetic local artifacts and mocked remote requests.
`tests/conftest.py` blocks unmocked `requests` HTTP calls.
The suite does not verify current live Komoot behavior.
Do not add tests that log in to real accounts, mutate tours or make live network requests.
Cover tool discovery, result envelopes and policy rejection through the MCP interface.
Use temporary directories for GPX files, receipts, pending intents and export output.
Retain failure coverage for uncertain writes and blocked retries.

## Package checks

Start with a clean `dist/` directory that contains no files from an earlier build.
Build the source distribution, then build the wheel from that source distribution:

```bash
uv build --sdist --out-dir dist
uv build --wheel dist/*.tar.gz --out-dir dist
uv run --no-sync python -m twine check dist/*.whl dist/*.tar.gz
```

On macOS, create and verify the checksum file:

```bash
(cd dist && shasum -a 256 *.whl *.tar.gz > SHA256SUMS)
(cd dist && shasum -a 256 -c SHA256SUMS)
```

On Linux, replace `shasum -a 256` with `sha256sum`.
Install the exact built wheel into a fresh environment:

```bash
uv venv --python 3.11 .venv-wheel
uv pip install --python .venv-wheel/bin/python "dist/komoot_mcp-<version>-py3-none-any.whl"
.venv-wheel/bin/komoot-mcp --help
```

Replace `<version>` with the package version.
The [CI workflow](https://github.com/realholgi/komoot-mcp/blob/main/.github/workflows/ci.yml) tests Python 3.11 and 3.14 on Linux.
It runs on main-branch pushes, pull requests and manual requests.
After the tests pass, Linux builds and checks the distributions.
It then installs the wheel and checks read-only MCP discovery without account or network changes.
CI retains the tested wheel, source archive and `SHA256SUMS` for seven days.

The [release workflow](https://github.com/realholgi/komoot-mcp/blob/main/.github/workflows/release.yml) checks that the tag matches the package version.
It reuses CI and attaches those tested files to a draft GitHub release.
Release notes come from GitHub. This workflow does not publish to PyPI.

## Contributor workflow

1. Read the affected module and its existing tests.
2. Keep behavior behind the existing interface unless the change requires a new contract.
3. Add offline regression coverage at the affected seam.
4. Update the relevant setup, reference or import guide when user behavior changes.
5. Run the offline suite and package checks before submitting the change.

Preserve unrelated local changes.
Keep credentials, downloaded account data and publication records out of commits.
Use [MCP setup](mcp-setup.md#update-or-roll-back) to test installation updates and rollback.
