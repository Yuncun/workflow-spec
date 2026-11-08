# Write, check and share a workflow

Draft guide for workflow authors, people or agents.

## 1. Write it

```yaml
name: your-team/pr-summary
version: 1.0.0
description: Summarize a pull request's diff and post the summary as a comment.
inputSchema:
  type: object
  properties:
    repo: { type: string }
    pr: { type: integer, minimum: 1 }
  required: [repo, pr]
outputs:
  comment: { from: steps.post.outputs.url }
steps:
  diff:
    uses: your-team/fetch-diff@2.0.1
    with: { repo: { from: inputs.repo }, pr: { from: inputs.pr } }
  summary:
    uses: your-team/summarize-diff@1.0.0
    with: { diff: { from: steps.diff.outputs.diff }, audience: everyone }
  post:
    uses: your-team/post-comment@1.1.0
    with:
      repo: { from: inputs.repo }
      pr: { from: inputs.pr }
      body: { from: steps.summary.outputs.summary }
```

- **`uses` names an exact version.** Find steps and their inputs in the catalog, or through the MCP server's `find_steps` and `get_step`.
- **A value in `with` is one of three things:** a literal, `{ from: inputs.<name> }`, or `{ from: steps.<id>.outputs.<name> }`.
- **There are no conditions or loops.** A decision goes in a step: a small code step for a fixed rule, or an agent step for a judgment (SPEC.md, section 5.4).

## 2. Check it

```bash
wfspec check workflow.yaml --catalog <folder of step manifests>
```

The platform runs the same check before every run, and through the MCP server's `check_workflow`. Each problem has a code and a JSON Pointer to where it is:

| Code | Usual fix |
|---|---|
| `unknown_step` | use a version the message lists |
| `unknown_input` | fix the spelling the message suggests |
| `missing_input` | add the input under `with` |
| `bad_reference` | write `inputs.<name>` or `steps.<id>.outputs.<name>` |
| `unknown_reference` | fix the step id, input or output name |
| `type_mismatch` | connect a different output, or add a step that converts it |
| `invalid_value` | change the literal to fit the input's schema |
| `cycle` | remove the loop |
| `no_outputs` | add an output, or the workflow does nothing |

Warnings don't block a run:
- `type_unchecked`: a limit is checked when the step runs.
- `step_never_runs`: nothing uses the step.
- `unused_input`: nothing uses the input.

## 3. Run it

Run it through the platform: its API, its MCP server, or a form built from `inputSchema`. A rerun with some inputs changed reuses the cached results of steps whose inputs didn't change, if those steps are deterministic.

## 4. Share it

- Publish it with `name`, `version` and `description`. The platform writes a lock file that pins every step by content hash.
- The published workflow becomes a step that other workflows can use (SPEC.md, section 8.3).
- It also appears as a tool in Copilot and other MCP clients.
- People who only want to run it get a form built from its inputs.

## Save an agent's run as a workflow

When an agent step solves something you'll need again, save its run as a workflow (SPEC.md, section 8.5):
1. Open the run record and list the steps the agent called, in order.
2. Make each call a step with the same `uses`.
3. Where a value came from an earlier call, reference that call's output.
4. Where the agent wrote something new between calls, add a narrow agent step with a typed output, or a fixed value.
5. Check it, then rerun it with the original inputs and compare the outputs.

`examples/runs/investigate.run.json` and `examples/workflows/ci-triage.yaml` show one run before and after.
