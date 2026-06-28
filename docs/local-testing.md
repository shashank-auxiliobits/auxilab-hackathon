# Local Testing & Connecting a Claude Client

## 1. Set up and verify everything (one command)

```bash
./scripts/setup.sh --all       # or: make setup
```

`--all` installs `uv` + deps, generates `.env` with secrets, starts Postgres,
runs migrations, seeds a demo org + API key, **runs the full test suite**, and
**runs the live end-to-end demo** — so you confirm the whole system works in one
shot. Re-runnable and idempotent (never overwrites `.env`).

Flags: `--seed` (demo data), `--verify` (tests), `--demo` (live demo),
`--no-start` (skip Postgres). Plain `./scripts/setup.sh` does setup only.

Then run the services, each in its own shell:

```bash
make run-api                   # REST API  → http://127.0.0.1:8000/docs
make run-mcp                   # MCP server → http://127.0.0.1:8080/mcp
```

## 2. See the whole pipeline work (one command)

With the API running:

```bash
make demo        # or: uv run python scripts/demo.py
```

It provisions a fresh org, onboards a vendor, **attaches a policy document and
compiles structured rules**, approves a rule, then processes several invoices and
prints each decision:

```
clean $1,250                 → auto_approve [approved]
$9,000 (over compiled cap)   → flag         [flagged]   (exceeds policy cap 5000)
duplicate of INV-1001        → reject       [rejected]
unknown vendor (auto-onboard)→ hold         [held]
```

## 3. Run the tests

```bash
make test        # unit (no DB)
make test-int    # integration (needs `make db-up` + the ap_invoice_test database)
```

---

## 4. Connect a Claude client to the MCP server

> **Tip:** the **streamable-HTTP** path is the simplest locally. Start the server
> with `make run-mcp`, then get an org API key from `make seed`.

### Claude Code (CLI) — HTTP (recommended)

```bash
KEY=ap_xxx.yyy     # an org API key (from `make seed` or POST /api-keys)

claude mcp add --transport http ap-invoice \
  http://localhost:8080/mcp \
  --header "Authorization: Bearer $KEY"

claude mcp list                 # verify it's registered
# inside a Claude Code session:  /mcp   → should show ap-invoice connected
# remove with:  claude mcp remove ap-invoice
```

### Claude Code (CLI) — stdio

Runs the server as a subprocess. `uv run --directory` makes it load this
project's venv and `.env` (so DB URL + pepper come from `.env`); you only add the
stdio transport flag and an org key:

```bash
claude mcp add --transport stdio ap-invoice \
  --env AP_MCP_TRANSPORT=stdio \
  --env AP_MCP_API_KEY=ap_xxx.yyy \
  -- uv run --directory /ABSOLUTE/PATH/TO/auxilab-hackathon ap-invoice-mcp
```

### Claude Desktop — stdio

Config file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "ap-invoice": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/auxilab-hackathon", "ap-invoice-mcp"],
      "env": {
        "AP_MCP_TRANSPORT": "stdio",
        "AP_MCP_API_KEY": "ap_xxx.yyy"
      }
    }
  }
}
```

Restart Claude Desktop; the `ap-invoice` tools appear in the tools menu.
(DB URL + key pepper are read from the project's `.env`; or add them to `env` here.)

> Claude Desktop does **not** natively support remote HTTP MCP servers with auth
> headers — use the stdio config above, or proxy via `mcp-remote`.

### Claude API / Agent SDK (Python) — HTTP

```python
import anthropic

client = anthropic.Anthropic()
response = client.beta.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{"role": "user",
               "content": "Process this invoice and tell me the decision:\n<invoice text>"}],
    mcp_servers=[{
        "type": "url",
        "url": "http://localhost:8080/mcp",
        "name": "ap-invoice",
        "authorization_token": "ap_xxx.yyy",   # the org API key (no 'Bearer ' prefix)
    }],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "ap-invoice"}],
    betas=["mcp-client-2025-11-20"],
)
print(response.content[-1].text)
```

> The MCP connector requires a publicly reachable URL for hosted Claude; for a
> purely local server use Claude Code / Desktop, or expose the port via a tunnel.

## 5. What the agent can do once connected

The agent sees 10 org-scoped tools, e.g.:
`process_invoice_text` (the main one), `update_invoice_status`,
`invoice_stats`, `list_invoices`, `normalise_vendor_name`,
`detect_duplicate_invoice`, plus the individual extract/terms/completeness tools.
See [MCP Tools](./mcp-tools.md).
