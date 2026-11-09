# Publish a step from your code

Draft guide for step authors. Where it says "the platform", substitute the platform you publish to.

A repository becomes a step when it has a manifest, `step.yaml`, next to the code. The manifest says:
- what the step takes
- what it gives back
- whether it reads or changes anything outside its inputs
- how to run it

## 1. Write the manifest

```yaml
name: your-team/diff-stats
version: 1.0.0
title: Count a diff's changes
description: Count the files, added lines and removed lines in a git diff. Use it to size a change before reviewing it.
inputSchema:
  type: object
  properties:
    diff: { $ref: urn:wfspec:file, x-mediaType: text/x-diff }
  required: [diff]
outputSchema:
  type: object
  properties:
    files: { type: integer, minimum: 0 }
    added: { type: integer, minimum: 0 }
    removed: { type: integer, minimum: 0 }
  required: [files, added, removed]
effects: none            # it only reads its input
deterministic: true      # the same diff always gives the same counts, so results are cached
runtime:
  container:
    image: ghcr.io/your-team/diff-stats:1.0.0
    command: [python, -m, diff_stats]
examples:
  - inputs:
      diff: { digest: "sha256:9f2c4e8a1b7d3f6025e8c4a1d9b3e7f0a2c5d8e1b4f7a0c3d6e9b2f5a8c1d4e7", mediaType: text/x-diff }
```

- **Write `description` for an agent.** Agents choose steps by reading it, so say what the step does and when to use it.
- **Give inputs and outputs real types.** Use enums, ranges and required fields. Each one you leave out turns an error in someone's workflow into a failure at run time.
- **Use only `{$ref: urn:wfspec:file}` for references** in schemas. No other `$ref`, `$defs` or `$id` is allowed (SPEC.md, [manifest.refs]).

## 2. Choose its effects

| Your code... | `effects` | `deterministic` |
|---|---|---|
| only computes from its inputs | `none` | usually true |
| reads data that can't change for the same inputs | `reads` | true |
| reads data that can change, or calls a model | `reads` | false |
| creates, changes or deletes anything outside | `writes` | false |

A `writes` step is never cached or retried automatically, and a platform may ask for approval before it runs.

If your step can find it has nothing to do, such as an empty list, set **`maySkip: true`** and skip instead of failing (step 3). The steps that depend on it are skipped too, and the run still succeeds.

## 3. Read inputs and write outputs

The platform passes input values in a JSON file and reads outputs from another. This contract is in drafts/runtime.md, section 1, a draft that the reference runner is testing:

```python
import json, os

with open(os.environ["WF_INPUTS"]) as f:
    inputs = json.load(f)                 # each file input carries a local "path"

files = added = removed = 0
with open(inputs["diff"]["path"]) as diff:
    for line in diff:
        if line.startswith("diff --git"):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1

with open(os.environ["WF_OUTPUTS"], "w") as f:
    json.dump({"files": files, "added": added, "removed": removed}, f)
```

- **File outputs:** write `{"path": "...", "mediaType": "..."}`. The platform stores the file and passes it on by its digest.
- **Secrets:** those named in `secrets` arrive as environment variables. Your step gets no others, so a step that calls a model must name the model's key.
- **Failing and skipping:** exit with a non-zero code to fail. To skip, write a reason to the file named by `WF_SKIP`.

## 4. Check the manifest

```bash
wfspec check-step step.yaml
wfspec check-step step.yaml --catalog <catalog folder>   # for a step with calls: also checks the steps it calls
```

## 5. Publish

The platform does four things when you publish:
- builds the image at your commit and pins it by digest
- checks your `examples` against the schemas. Running them as tests is a proposal (proposals.md, P14).
- refuses to reuse a name@version that already holds different content
- adds `your-team/diff-stats@1.0.0` to the catalog

After a change that breaks callers, publish under a new major version (SPEC.md, section 10.5). Old versions stay available, so published workflows keep working.

## Agent steps

An agent step has the same manifest with `runtime.agent` instead of `runtime.container`:
- **`instructions`:** a file next to the manifest, used as the agent's system prompt.
- **`model`:** the model to run.
- **`maxTurns`:** an optional limit on the agent's responses.

To let the agent use other steps as tools, list their exact versions in **`calls`**. Two rules keep the manifest honest, and they apply to any step with `calls`, not only agents:
- Its `effects` must be at least those of each step it can call.
- Its `secrets` must include each of those steps' secrets.

The runner offers each step in `calls` as a tool, plus a `finish` tool whose parameters are your `outputSchema`. It checks the agent's final answer against the schema and hands any problem back for another try (drafts/runtime.md, section 2).

`examples/steps/` has an agent without calls (`summarize-diff.yaml`) and one with calls (`investigate-failure.yaml`).

## Let an agent draft it

Give your agent this, in the repository:

> Read SPEC.md section 5, drafts/runtime.md and this repository's README, command-line help and tests. Write `step.yaml` for the main command. Use the narrowest types the code supports, and choose `effects` with the table in guides/publish-a-step.md. Add one example from the tests. Run `wfspec check-step step.yaml` and fix every error. Then run the command on the example in a container and confirm the outputs fit `outputSchema`. Open a pull request so a person can review the manifest.

The person reviewing should check `effects` first. That one field decides whether the step is cached, retried, or needs approval.
