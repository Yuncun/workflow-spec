# Steps as MCP tools

Draft, 2026-11-08. **Not normative.** This document becomes a normative companion to [SPEC.md](../SPEC.md) once a platform's MCP server has tests.

A step manifest is an MCP tool definition plus a few fields (SPEC.md, section 5.1). This document has two sections:
- how a step is offered as a tool, which agent steps also rely on ([runtime.md](runtime.md), section 2)
- what a platform's MCP server offers agents such as Copilot

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

For `writes`, `destructiveHint` and `idempotentHint` are left out, so MCP's defaults apply: destructive and not idempotent. Version 0.1 set them from `destructive` and `idempotent`, which version 0.2 doesn't have (proposals.md, P2 and P3).

- **Why that name form.** It uses only letters, digits, `_` and `-`. Both MCP and model tool APIs accept those characters; MCP also allows `.`, but model APIs don't. Step names can't contain `_`, so the separators are unambiguous. On 2026-11-08, LM Studio's tool-call API accepted `acme_list-files_v1`.
- **Versions.** A server offers the latest version within each major version, so minor updates reach callers and breaking ones don't. That's why a step may list only one version per major version in `calls` (SPEC.md, [manifest.calls-one-per-major]).
- **Long names.** A name over 64 characters is cut, and 8 hex characters of the full name's SHA-256 are appended, so it stays unique.
- **Trust.** MCP treats annotations as untrusted unless the server is trusted. A platform's own server is trusted, and it enforces `effects` itself (SPEC.md, [run.effects]).

`wfspec mcp-tool <manifest>` prints the result.

## 2. A platform's MCP server

A platform should offer one MCP server through which agents find steps, check workflows and run them.

| Tool | Arguments | Returns |
|---|---|---|
| `find_steps` | `query`, optional `effects` | name@version, title, description, effects |
| `get_step` | `ref` | the manifest |
| `check_workflow` | `workflow` (text) | the checker's result (SPEC.md, section 8.3) |
| `run_workflow` | `workflow` (text) or `ref`, `inputs` | `{runId}`, or the checker's result if checking fails |
| `get_run` | `runId` | the run record so far |
| `put_file` | `name`, `mediaType`, content | a file value, for file inputs |

- **Each published workflow also appears as its own tool,** named as in section 1, with the workflow's derived interface (SPEC.md, section 10.4).
  - Calling the tool runs the workflow and returns its outputs as the tool's structured result.
  - When the run outlasts the call, the call returns a task, and the task's result carries the outputs.
  - A run that fails returns an error result that includes the run id.
- **Nothing runs unchecked.** `run_workflow` and the per-workflow tools check first, under the caller's policy, and return the errors instead of starting a run.
- **No editing tools are needed.** A workflow is a text file, which agents already edit. The server supplies what an agent can't know on its own: which steps exist, whether a file is correct, and what happened when it ran.
- **Long runs** can use MCP's tasks extension (`io.modelcontextprotocol/tasks`, revision 2026-07-28), where a call returns a handle the client polls. Clients that don't support it use `run_workflow` and poll `get_run`.
- **Forms.** A client that supports MCP Apps can show a published workflow's `inputSchema` as a form. Copilot Chat in VS Code supports MCP Apps.

These tools are the agent-facing part of a service API. Whether a service API should be standardized waits until a second platform exists (proposals.md, P18).
