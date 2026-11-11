# Steps as MCP tools

Draft, 2026-11-09. **Not normative.** This document becomes a normative companion to [SPEC.md](../SPEC.md) once a platform that offers steps as MCP tools has tests for it.

A step manifest is an MCP tool definition plus a few fields (SPEC.md, section 5.1). This document says how a step is offered as a tool, which agent steps also rely on ([runtime.md](runtime.md), section 2). What else a platform's MCP server offers is the platform's choice.

## 1. A step as an MCP tool

| MCP tool field | From the manifest |
|---|---|
| `name` | `<owner>_<name>_v<major>`, such as `acme_open-issue_v1` |
| `title`, `description` | the same |
| `inputSchema`, `outputSchema` | the same, with `urn:wfspec:file` references written out in full |
| `annotations` | from `effects`, as below |

| `effects` | `readOnlyHint` | `openWorldHint` |
|---|---|---|
| `none` | true | false |
| `reads` | true | true |
| `writes` | false | true |

For `writes`, `destructiveHint` and `idempotentHint` are left out, so MCP's defaults apply: destructive and not idempotent (proposals.md, P2 and P3).

- **Why that name form.** It uses only letters, digits, `_` and `-`. Both MCP and model tool APIs accept those characters; MCP also allows `.`, but model APIs don't. Step names can't contain `_`, so the separators are unambiguous.
- **Versions.** A server offers the latest version within each major version, so minor updates reach callers and breaking ones don't. That's why a step may list only one version per major version in `calls` (SPEC.md, [manifest.calls-one-per-major]).
- **Long names.** A name over 64 characters is cut, and 8 hex characters of the full name's SHA-256 are appended, so it stays unique.
- **Trust.** MCP treats annotations as untrusted unless the server is trusted. A platform's own server is trusted, and it enforces `effects` itself (SPEC.md, [run.effects]).

`wfspec mcp-tool <manifest>` prints the result.
