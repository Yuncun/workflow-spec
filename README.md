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

A workflow connects steps. Each input of a step gets either a literal value or a reference to a workflow input or to another step's output:

```yaml
name: acme/pr-summary
version: 1.0.0
description: Summarize a pull request's diff in plain words and post the summary as a comment on it.
inputSchema:
  type: object
  properties:
    repo: { type: string, pattern: "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$" }
    pr: { type: integer, minimum: 1 }
  required: [repo, pr]
outputs:
  comment: { from: steps.post.outputs.url }
steps:
  diff:
    uses: acme/fetch-pr-diff@1.2.0
    with: { repo: { from: inputs.repo }, pr: { from: inputs.pr } }
  summary:
    uses: acme/summarize-diff@1.0.0
    with: { diff: { from: steps.diff.outputs.diff }, audience: everyone, max_words: 150 }
  post:
    uses: acme/post-pr-comment@1.1.0
    with:
      repo: { from: inputs.repo }
      pr: { from: inputs.pr }
      body: { from: steps.summary.outputs.summary }
```

To write a workflow, start with [guides/write-a-workflow.md](guides/write-a-workflow.md). To build a checker or a runner, read [SPEC.md](SPEC.md).

## In this repository

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
