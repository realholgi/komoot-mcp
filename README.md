# komoot-mcp

[![CI](https://github.com/realholgi/komoot-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/realholgi/komoot-mcp/actions/workflows/ci.yml) [![Python](https://img.shields.io/badge/python-%3E%3D3.11-blue)](pyproject.toml)

An independent [MCP](https://modelcontextprotocol.io) server for Komoot tours and route planning.

Browse tours and highlights, download GPX/FIT files, or plan routes with Komoot's native planner.
Import prepared routes from an external exporter as private planned tours.
The importer reads saved tours back to check POIs and geometry.
It needs no exporter installation, source database or running process.
Photos and full detail archives stay local.

## Quickstart

Use Python 3.11 or later. Clone the repository and install the package:

```bash
git clone https://github.com/realholgi/komoot-mcp.git
cd komoot-mcp
python -m venv .venv
source .venv/bin/activate
python -m pip install .
komoot-mcp --help
```

Add the installed command to your MCP client:

```json
{
  "mcpServers": {
    "komoot-mcp": {
      "command": "/absolute/path/to/komoot-mcp/.venv/bin/komoot-mcp",
      "args": ["--transport", "stdio", "--read-only"]
    }
  }
}
```

Set `KOMOOT_EMAIL` and `KOMOOT_PASSWORD` in the server's launch environment for account access.
The `--read-only` flag hides and blocks tools that can change accounts or local files.
See [MCP setup](docs/mcp-setup.md) for installation without a checkout, credentials and write-enabled operation.

## Example requests

- "List my planned hiking tours."
- "Create a private collection with these tours, then reorder them after I approve the exact changes."
- "Plan a hiking route through these coordinates without saving it."
- "Brief this tour with supplies, critical sections and a pause schedule."
- "Split this tour into three days, with tent camping or accommodation."
- "Preview this prepared route set before I approve publication."

Remove `--read-only` before publication.
Get explicit user approval before uploading, saving, updating, deleting, sharing or bookmarking.
Use the client's tool discovery for current argument schemas.

## Documentation

| Guide | Contents |
| --- | --- |
| [MCP setup](docs/mcp-setup.md) | Installation, client configuration, HTTP gateway and troubleshooting |
| [Reference](docs/reference.md) | Tools, results, resources, prompts and coordinate pages |
| [Prepared-route import](docs/prepared-import.md) | Exporter workflow, preview, approval, receipts and recovery |
| [Prepared artifact format](docs/prepared-artifacts.md) | The version 1 file handoff |
| [Development](docs/development.md) | Architecture, locked dependencies, offline tests and package checks |

Report bugs in [GitHub Issues](https://github.com/realholgi/komoot-mcp/issues) with synthetic GPX files and no credentials.
