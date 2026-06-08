# MCP Tools Reference

The MCP server exposes the invoice-intelligence capabilities as tools an AI
agent can call. It supports two transports (set `AP_MCP_TRANSPORT`):

- **streamable-http** (default) — multi-tenant, production. Endpoint: `/mcp` on
  `AP_MCP_PORT` (default 8080). Agents authenticate per request with
  `Authorization: Bearer <org API key>`.
- **stdio** — local development (e.g. Claude Desktop). The key comes from
  `AP_MCP_API_KEY`.

Every tool call is scoped to the authenticated organization.

## Tools

| Tool | Purpose |
|------|---------|
| `extract_invoice_fields(raw_text, engine?)` | Parse raw invoice text → structured fields + per-field confidence. |
| `normalise_vendor_name(raw_name, threshold?)` | Resolve a messy vendor name to the org's canonical vendor; flag unknowns. |
| `detect_duplicate_invoice(vendor_name?, invoice_number?, amount?, date?, amount_tolerance_pct?, lookback_days?)` | Find exact/near duplicates among the org's recent invoices. |
| `calculate_payment_terms_tool(invoice_date, payment_terms, amount?, as_of?)` | Due date, discount deadline/amount, days remaining. |
| `check_invoice_completeness(fields, mandatory_fields?)` | Completeness %, missing fields, recommended action. |
| `process_invoice_text(raw_text, actor?, idempotency_key?, engine?, source?, auto_onboard?)` | **End-to-end**: extract → normalise → completeness → duplicates → terms → policy decision (incl. approved structured rules), persisted with an audit trail. `auto_onboard` (default true) auto-creates an unknown vendor as 'onboarding' so processing doesn't halt. The primary action for automating approvals. |
| `update_invoice_status(invoice_id, status, note?, actor?)` | Set an invoice's status: `approved` / `held` / `flagged` / `rejected` (no payment). Recorded in the audit trail. |
| `invoice_stats()` | Counts of the org's invoices by status (+ totals) — answers "how many flagged/approved invoices?". |
| `list_invoices(status?, vendor_id?, limit?, offset?)` | Page through the org's invoices, e.g. to fetch all `flagged` or `approved` ones for further processing. |
| `list_vendors()` | The org's vendors and statuses, to ground decisions. |

> All DB-backed tools (`*_vendor*`, `*_invoice*`, `invoice_stats`, `list_invoices`)
> are **scoped to the authenticated organization** — an agent only ever sees and
> acts on its own org's vendors, policies, and invoices.

## Connecting (streamable-HTTP)

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

headers = {"Authorization": "Bearer ap_<prefix>.<secret>"}
async with streamablehttp_client("http://localhost:8080/mcp", headers=headers) as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        result = await session.call_tool(
            "process_invoice_text",
            {"raw_text": invoice_text, "actor": "agent:claude"},
        )
        print(result.structuredContent)  # the explained decision
```

## Connecting with Claude Desktop (stdio)

Add to your MCP client config (set the env so the server scopes calls to an org):

```json
{
  "mcpServers": {
    "ap-invoice": {
      "command": "ap-invoice-mcp",
      "env": {
        "AP_MCP_TRANSPORT": "stdio",
        "AP_DATABASE_URL": "postgresql+asyncpg://ap:ap_password@localhost:5432/ap_invoice",
        "AP_API_KEY_PEPPER": "<your pepper>",
        "AP_MCP_API_KEY": "ap_<prefix>.<secret>"
      }
    }
  }
}
```

## How an agent uses these tools

A typical autonomous flow: call `process_invoice_text` with the raw invoice; read
the returned decision and reasons. For `hold`/`flag`/`reject` outcomes the agent
can dig in with `list_vendors`, `detect_duplicate_invoice`, or
`check_invoice_completeness` to explain the exception to a human, or onboard a new
vendor via the REST API. Because the verdict is deterministic and every step is
logged, the agent's actions are fully auditable.
