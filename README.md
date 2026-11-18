# komoot-mcp

An independent [MCP](https://modelcontextprotocol.io) server for Komoot tours and route planning.

Browse tours and highlights, download GPX/FIT files, plan routes, or manage your account's tours.
Import prepared routes from an external exporter as private planned tours.
The importer reads the saved tours back to check POIs and geometry.

## Install

Use Python 3.11 or later. Run these commands from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
komoot-mcp --help
```

The package uses Komoot-native route planning.

## Connect an MCP client

Use the absolute path to the installed command:

```json
{
  "mcpServers": {
    "komoot-mcp": {
      "command": "/absolute/path/to/komoot-mcp/.venv/bin/komoot-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

For account tools, set `KOMOOT_EMAIL` and `KOMOOT_PASSWORD` in the server's launch environment.
Get explicit user approval before uploading, saving, updating, deleting, sharing or bookmarking.

Use the client's tool discovery for current names and argument schemas.
See [MCP setup](docs/mcp-setup.md) for credentials, routing and the authenticated HTTP gateway.

## Import prepared routes

The server accepts versioned `prepared.json` artifacts with their referenced GPX and detail files.
It does not need the exporter's snapshots, stage database, running process, Python library or source checkout.
For HexaTrek stages, follow the [hexatrek-exporter workflow](docs/prepared-import.md#use-routes-from-hexatrek-exporter).

- [Prepared-route import](docs/prepared-import.md): approval, publication receipts, updates and recovery.
- [Prepared artifact format](docs/prepared-artifacts.md): the version 1 file handoff.

Keep the same publication directory for each route set.
See the import guide for receipts, blocked retries and recovery.
Photos and full detail archives stay local.

See the [development guide](docs/development.md) for an editable installation and tests.
