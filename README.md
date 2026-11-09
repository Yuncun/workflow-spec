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

Read [SPEC.md](SPEC.md) first. If you're a team adopting the format, read [HANDOFF.md](HANDOFF.md) next.

| Document | Status |
|---|---|
| [SPEC.md](SPEC.md): the format layer | normative; version 0.3 draft |
| [drafts/runtime.md](drafts/runtime.md): how a runner starts a step | draft, not normative |
| [drafts/mcp.md](drafts/mcp.md): steps as MCP tools | draft, not normative |
| [proposals.md](proposals.md): features left out, open questions | not normative |
| [CHANGELOG.md](CHANGELOG.md): changes and the versioning policy | |
| [CONTRIBUTING.md](CONTRIBUTING.md): how to change the spec | |

```text
schemas/                JSON Schemas: step, workflow, file value, lock file, run record
examples/
├─ steps/               seven step manifests: four containers, two agents without calls, an agent with calls
├─ workflows/           an agent-led workflow, the fixed workflow saved from its run, a PR summary
├─ runs/                run records for the first two
├─ ci-triage.lock.json  the lock file for the fixed workflow
└─ regenerate.py        recomputes the digests above after an example changes
guides/                 drafts for step authors and workflow authors
tests/                  test cases for checkers and runners, and coverage.yaml
reference/              a small Python checker and runner that pass them
```

## License

Apache-2.0, the license OpenAPI and Arazzo use. See [LICENSE](LICENSE).
