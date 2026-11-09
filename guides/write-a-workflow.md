# Write, check and share a workflow

Draft guide for workflow authors, people or agents.

## 1. Write it

This is `examples/workflows/pr-summary.yaml`. It checks clean against `examples/steps/`:

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

**Naming steps:**
- `uses` is the step's `name` and `version`, exactly as its manifest gives them. Find steps through your platform's catalog.
- When a step has several versions, use the newest, unless your workflow needs an older one's interface. Older versions stay so that published workflows keep working.

**Writing values in `with`:**
- A literal is any YAML value: `audience: everyone`, `max_words: 150`, a list, or an object such as a lookup table.
- Quote text that YAML would misread: `channel: "#builds"`, since an unquoted `#` starts a comment.
- `{ from: inputs.<name> }` passes a workflow input.
- `{ from: steps.<id>.outputs.<name> }` passes another step's output, whole.
- To pass a literal object whose only key is `from`, wrap it: `{ literal: { from: x } }`.

**Which steps run:**
- A step that changes outside data always runs, even if nothing uses its outputs.
- A step that only reads runs when an output, or a step that writes, needs it.

**There are no conditions or loops.** Put a decision in a step:
- **A fixed rule** goes in a small code step. Some steps already decide for themselves; for example, one might open an issue only if there are failures. Read step descriptions for that.
- **A lookup** goes in a step that maps one value to another.
- **A judgment** goes in an agent step with a typed output.
- **"Only if there's something to do"** is a step with `maySkip`. When it skips, the steps after it are skipped too.

**Converting between types.** When types don't line up, add a step that converts. For example, put a step that reads a file into text between a step that gives a file and one that takes text.

## 2. Check it

```bash
wfspec check workflow.yaml --catalog <folder of step manifests>
```

The platform runs the same check before every run. Each problem has a code and a JSON Pointer to where it is:

| Code | Usual fix |
|---|---|
| `unknown_step` | use a version the message lists, or another step if the manifest was left out |
| `unknown_input` | fix the spelling the message suggests |
| `missing_input` | add the input under `with` |
| `bad_reference` | write `inputs.<name>` or `steps.<id>.outputs.<name>` |
| `unknown_reference` | fix the step id, input or output name |
| `type_mismatch` | connect a different output, or add a step that converts it |
| `invalid_value` | change the literal to fit the input's schema |
| `cycle` | remove the loop |
| `no_outputs` | add an output, or the workflow does nothing |

When there are no errors, the checker prints the order a run would follow.

Warnings don't block a run:
- **`type_unchecked`:** the shapes fit, but a limit on the input, such as a maximum length or a pattern, can't be proven. The value is checked when the step runs. To clear the warning, give the source the same limit. For a workflow input, add the limit to `inputSchema`.
- **`step_never_runs`:** nothing uses the step and it doesn't write.
- **`unused_input`:** nothing uses the input.

## 3. Run it

Run it through the platform: its API, its MCP server, or a form built from `inputSchema`.
- A rerun with some inputs changed reuses the cached results of deterministic steps whose inputs didn't change.
- An output whose source was skipped, or is optional and wasn't produced, is left out of the run's outputs.

## 4. Share it

- Publish it with `name`, `version` and `description`. The platform writes a lock file that pins, by content hash, every step it can run, including the steps its steps can call.
- The published workflow becomes a step that other workflows can use (SPEC.md, section 10.4).
- On a platform with an MCP server, it also appears as a tool in MCP clients such as Copilot (drafts/mcp.md).
- People who only want to run it get a form built from its inputs.

## Save an agent's run as a workflow

When an agent step solves something you'll need again, save its run as a workflow (SPEC.md, section 10.6):
1. Open the run record and list the steps the agent called, in order.
2. Make each call a step with the same `uses`.
3. Where a value came from an earlier call, reference that call's output.
4. Where the agent wrote something new between calls, add a narrow agent step with a typed output, or a fixed value.
5. Check it, then do a dry run with the original inputs. It compares the outputs of deterministic steps, what each step that writes would be given, and whether any write is left out, with the original run. Steps that write return their recorded outputs instead of running again.

`examples/runs/investigate.run.json` and `examples/workflows/ci-triage.yaml` show one run before and after.
