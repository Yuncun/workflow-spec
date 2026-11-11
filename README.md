# Workflow format for shared jobs

A text format for jobs built from published steps. A step can be:
- a program
- an agent
- a tool on a remote MCP server
- another workflow

The package also gives the rules for:
- checking a job before it runs
- what running it must produce, the same for everyone
- saving an agent's run as a job anyone can rerun

Read [SPEC.md](SPEC.md) first.

| Document | Status |
|---|---|
| [SPEC.md](SPEC.md): the format layer | normative; version 0.4 draft |
| [drafts/runtime.md](drafts/runtime.md): how a runner starts a step | draft, not normative |
| [drafts/mcp.md](drafts/mcp.md): steps as MCP tools | draft, not normative |
| [proposals.md](proposals.md): features left out, open questions | not normative |
| [CHANGELOG.md](CHANGELOG.md): changes and the versioning policy | |
| [CONTRIBUTING.md](CONTRIBUTING.md): how to change the spec | |

```text
schemas/     JSON Schemas for each kind of document
examples/    step manifests, workflows, run records and a lock file
guides/      for step authors and workflow authors
tests/       test cases for checkers and runners
reference/   a small Python checker and runner that pass them
```

## License

Apache-2.0, the license OpenAPI and Arazzo use. See [LICENSE](LICENSE).
