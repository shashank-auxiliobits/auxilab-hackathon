# Documentation

AP Invoice Intelligence — a headless platform that lets AI agents automate
Accounts-Payable invoice processing against per-vendor policies, via a REST API
and an MCP server.

## Contents

| Doc | What it covers |
|-----|----------------|
| [Architecture](./architecture.md) | System design, components, request flow, design decisions |
| [Data Model](./data-model.md) | Entities, relationships, the audit trail |
| [Configuration](./configuration.md) | Every environment variable |
| [REST API Reference](./api-reference.md) | Endpoints, auth, examples |
| [MCP Tools Reference](./mcp-tools.md) | The tools agents call, and how to connect |
| [Policy Engine](./policy-engine.md) | How decisions are made (auto-approve / hold / flag / reject) |
| [Deployment](./deployment.md) | Docker, migrations, production hardening |
| [Contributing](../CONTRIBUTING.md) | Dev setup, workflow, quality gates |
| [Security](../SECURITY.md) | Reporting vulnerabilities, security model |

## The 30-second mental model

```
Raw invoice text
      │
      ▼
[Extract] → [Normalise vendor] → [Completeness] → [Duplicates] → [Payment terms]
      │                                                                  │
      └──────────────────────────► [Policy engine] ◄────────────────────┘
                                          │
                          Auto-Approve / Hold / Flag / Reject
                                          │
                                  (every step logged to an
                                   immutable audit trail)
```

An AI agent drives this by calling tools over MCP; the **decisions are
deterministic and rule-based** so they're explainable and reproducible.
