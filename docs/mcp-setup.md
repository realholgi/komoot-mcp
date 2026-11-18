# MCP setup

[Project overview](../README.md) | [Reference](reference.md) | [Prepared-route import](prepared-import.md)

## Install from a checkout

Use Python 3.11 or later. Run these commands from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install .
komoot-mcp --help
```

For an editable installation, see [Development](development.md).

## Install a downloaded wheel

No releases have been published yet.
When available, download the wheel and its `SHA256SUMS` from the same [GitHub release](https://github.com/realholgi/komoot-mcp/releases).
Keep older verified wheels if you need rollback.

The following commands use local downloaded files and need no source checkout.
Replace the example directory and version with the files you downloaded.
On macOS:

```bash
cd /absolute/path/to/downloaded-release
shasum -a 256 -c SHA256SUMS
uv tool install --python 3.11 "./komoot_mcp-<version>-py3-none-any.whl"
komoot-mcp --help
```

Run the checksum command in the directory that contains all files listed in `SHA256SUMS`.
Stop if any checksum fails or a listed file is missing.
On Linux, use `sha256sum -c SHA256SUMS`.
The checksum detects changed bytes. It does not authenticate the download source by itself.

`uv tool install` creates an isolated environment and exposes `komoot-mcp` on your tool `PATH`.
Use `command -v komoot-mcp` to find the command for client configuration.
If the shell cannot find it, follow `uv`'s tool-directory PATH instructions.

### Update or roll back

Stop the server before replacing its installation.
Verify the new release's checksums first. Then install its local wheel:

```bash
uv tool install --force --python 3.11 "/absolute/path/to/verified/komoot_mcp-<version>-py3-none-any.whl"
komoot-mcp --help
```

For rollback, run the same command with the retained, verified previous wheel.
For an existing virtual environment, use:

```bash
uv pip install --python /absolute/path/to/environment/bin/python --reinstall "/absolute/path/to/verified/komoot_mcp-<version>-py3-none-any.whl"
```

Restart or reload the MCP connection in your client, including OMP.
Use tool discovery, read `komoot://capabilities`, and try a read-only call such as `komoot_geocode`.
Do not test an update by changing the account.

## Connect through stdio

Use the absolute path to the installed command.
This example starts with the read-only policy:

```json
{
  "mcpServers": {
    "komoot-mcp": {
      "command": "/absolute/path/to/komoot-mcp",
      "args": ["--transport", "stdio", "--read-only"]
    }
  }
}
```

The stdio server uses the launching user's privileges and environment.
For stronger isolation, use a dedicated environment or account.
Starting the server or listing tools does not change accounts.

### Credentials and local files

Set these variables in the server's launch environment as needed:

| Variable | Purpose |
| --- | --- |
| `KOMOOT_EMAIL` | Komoot account email |
| `KOMOOT_PASSWORD` | Komoot account password |
| `KOMOOT_RATE_LIMIT` | Outbound Komoot requests per second, default `2` |

`KOMOOT_DATA_DIR` is unused. Prepared publication uses an explicit `publication_directory`.
Keep credentials out of Git, GPXs and publication receipts.
Keep publication records private and retain them for retries and updates.

### Read-only policy and approval

`--read-only` is a startup policy for stdio and HTTP.
The default, without this flag, exposes the full tool catalog.
Read-only mode advertises only tools whose `readOnlyHint` is explicitly `true`.
It also refuses direct calls to hidden write tools before argument handling, authentication, local files or backend work.
The failure uses `permission_denied` with `operation_state=not_attempted`.

Read-only mode is not an offline mode. Visible tools can contact Komoot or public Photon.
Downloads that can save local files stay hidden even when a call would request inline content.
Tool hints and prompts do not grant permission.

To enable changes, stop the server, remove `--read-only`, and reconnect.
Get explicit user approval before uploading, saving, updating, deleting, sharing or bookmarking.
Planning tools that save tours also need approval.
After approval, set `confirmed=true` for `komoot_publish_prepared`.
For updates, also set `update=true`.
Follow the [prepared-import guide](prepared-import.md) for review and recovery.

## HTTP gateway mode

Run HTTP mode for gateway integrations:

```bash
komoot-mcp --transport http --host 127.0.0.1 --port 3007 --read-only
```

The MCP endpoint is `/mcp`. The health endpoint is `/health`.
`MCP_HOST` and `MCP_PORT` set launch defaults.
Expose HTTP only behind TLS.
Every request except `/health` needs both headers:

| Header | Value |
| --- | --- |
| `Authorization` | `Bearer <INTERNAL_SECRET>` or `Bearer Internal-gateway:<GATEWAY_SECRET>` |
| `X-User-Credentials` | JSON with nonempty `email` and `password` |

Configure at least one gateway secret in the server environment.
Requests are stateless. The server keeps each request's credentials in its own context.
Missing or invalid credentials never fall back to server Komoot credentials.
HTTP authentication still applies in read-only mode.

Local filesystem operations are unavailable through HTTP.
`komoot_preview_prepared`, `komoot_publish_prepared` and `komoot_adopt_receipt` reject HTTP calls before reading files or getting account credentials.
Use inline content for tools that offer it.

Direct Komoot REST/native requests use a user ID and session token.
Legacy kompy calls use account credentials with fixed Komoot endpoints.
Direct transports check HTTPS destinations and refuse redirects.
They ignore ambient proxies and `.netrc` credentials.
Geometry endpoints use numeric IDs instead of links from API responses.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| The client cannot start the server | Check the absolute command path and run that command with `--help` |
| A tool is missing | Read `komoot://capabilities`. Check `--read-only`, then refresh tool discovery after reconnecting |
| `unknown_parameters` | Use tool discovery and remove unsupported argument names |
| `invalid_input` | Correct the named caller input. Do not treat an internal validation failure as bad arguments |
| `authentication_failed` | Check the server launch credentials or the HTTP request credentials |
| HTTP rejects a call | Check both gateway headers and secrets. Filesystem operations require local stdio |
| A coordinate result is incomplete | Follow `next_offset`. See [Pagination](reference.md#pagination) for limits and consistency |
| A publication failed or stopped | Keep its publication directory. Follow [Updates and recovery](prepared-import.md#updates-and-recovery) before retrying |

See [Reference](reference.md) for tool groups, units, planning, complete GPX results, error codes, resources and prompts.
