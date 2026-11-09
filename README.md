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

## Try it

```bash
uv venv .venv && uv pip install --python .venv/bin/python -e "reference[test]"

# check a workflow (0 errors, 0 warnings)
.venv/bin/wfspec check examples/workflows/ci-triage.yaml --catalog examples/steps

# see errors an agent would get
.venv/bin/wfspec check tests/check/unknown-references/workflow.yaml \
  --catalog examples/steps --catalog tests/catalog

# the manifest a workflow gets when published, and a step as an MCP tool
.venv/bin/wfspec as-step examples/workflows/ci-triage.yaml --catalog examples/steps
.venv/bin/wfspec mcp-tool examples/steps/open-issue.yaml

# run a workflow with the reference runner (programs only; agent steps need a model server)
.venv/bin/wfspec run tests/run/order-and-values/workflow.yaml --catalog tests/run-catalog --input text=hi

# run every test case, example check and the coverage check
.venv/bin/python -m pytest reference/tests
```

## Status

- Version 0.3, a draft dated 2026-11-09.
- The reference checker passes every test case, and so does the reference runner on macOS. Elsewhere the runner can't enforce effects for programs, so it skips the case that tests it.
- 85 of SPEC.md's 89 requirements have tests. [tests/coverage.yaml](tests/coverage.yaml) says why the other four have none.
- The runner runs on one machine. It runs `process` steps (programs), `agent` steps (any OpenAI-compatible model API, such as LM Studio's) and workflows used as steps.
- On macOS it runs programs that don't write in the system sandbox:
  - They can't write outside their work folder or signal other processes.
  - They can't read under your home folder, except their own step's folder and folders you grant with `--allow-read`.
  - A `none` program also gets no network, and can't read the shared temporary folders or other volumes.
  - The sandbox can't stop a program from reading the environment your other programs started with.

  Steps that write run with your full access, so run only steps you trust.

## License

Apache-2.0, the license OpenAPI and Arazzo use. See [LICENSE](LICENSE).
