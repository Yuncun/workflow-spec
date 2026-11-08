# Publish a step from your code

Draft guide for step authors. Where it says "the platform", substitute the platform you publish to.

A repository becomes a step when it has a manifest, `step.yaml`, next to the code. The manifest says:
- what the step takes
- what it gives back
- whether it reads or changes anything outside its inputs
- how to run it

## 1. Write the manifest

```yaml
name: your-team/summarize-diff
version: 1.0.0
description: Summarize a git diff in plain English for a pull request description.
inputSchema:
  type: object
  properties:
    diff: { $ref: urn:wfspec:file, x-mediaType: text/x-diff }
    audience: { type: string, enum: [engineers, everyone], default: engineers }
  required: [diff]
outputSchema:
  type: object
  properties:
    summary: { type: string, minLength: 1 }
  required: [summary]
effects: reads           # it calls a model
deterministic: false
runtime:
  container:
    image: ghcr.io/your-team/summarize-diff:1.0.0
    command: [python, -m, summarize_diff]
examples:
  - inputs:
      audience: everyone
      diff: { digest: "sha256:9f2c4e8a1b7d3f6025e8c4a1d9b3e7f0a2c5d8e1b4f7a0c3d6e9b2f5a8c1d4e7", mediaType: text/x-diff }
```

- **Write `description` for an agent.** Agents choose steps by reading it, so say what the step does and when to use it.
- **Give inputs and outputs real types.** Use enums, ranges and required fields. Each one you leave out turns an error in someone's workflow into a failure at run time.

## 2. Choose its effects

| Your code... | `effects` | `deterministic` |
|---|---|---|
| only computes from its inputs | `none` | usually true |
| reads data that can't change for the same inputs | `reads` | true |
| reads data that can change, or calls a model | `reads` | false |
| creates, changes or deletes anything outside | `writes` | false |

For a `writes` step, two more fields matter:
- **`idempotent: true`** if repeating a call changes nothing more. A step that does this can be retried automatically.
- **`destructive: false`** if the step only adds things.

## 3. Read inputs and write outputs

The platform passes input values in a JSON file and reads outputs from another:

```python
import json, os

with open(os.environ["WF_INPUTS"]) as f:
    inputs = json.load(f)              # file inputs carry a local "path"

summary = summarize(open(inputs["diff"]["path"]).read(), audience=inputs["audience"])

with open(os.environ["WF_OUTPUTS"], "w") as f:
    json.dump({"summary": summary}, f)  # for a file output: {"path": ..., "mediaType": ...}
```

- Secrets named in `secrets` arrive as environment variables.
- Exit with a non-zero code to report failure.

## 4. Check the manifest

```bash
wfspec check-step step.yaml
```

## 5. Publish

The platform does four things when you publish:
- builds the image at your commit
- runs your `examples` and checks the outputs against `outputSchema`
- pins the image by digest
- adds `your-team/summarize-diff@1.0.0` to the catalog

After a change that breaks callers, publish under a new major version (SPEC.md, section 8.4). Old versions stay available, so published workflows keep working.

## Let an agent draft it

Give your agent this, in the repository:

> Read SPEC.md section 3 and this repository's README, command-line help and tests. Write `step.yaml` for the main command. Use the narrowest types the code supports, and choose `effects` with the table in guides/publish-a-step.md. Add one example from the tests. Run `wfspec check-step step.yaml` and fix every error. Then run the command on the example in a container and confirm the outputs fit `outputSchema`. Open a pull request so a person can review the manifest.

The person reviewing should check `effects` first. That one field decides whether the step is cached, retried, or needs approval.
