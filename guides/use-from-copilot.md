# Use workflows from Copilot

Draft guide for people who run and build workflows through Copilot or another MCP client. It describes the MCP server a platform builds from SPEC.md, section 9.2.

## What the server offers

| Tool | Use it to |
|---|---|
| `find_steps` | find published steps by what they do |
| `get_step` | read a step's inputs, outputs and effects |
| `check_workflow` | check a workflow file before running it |
| `run_workflow` | run a workflow file or a published workflow |
| `get_run` | follow a run and read its outputs and errors |

Every published workflow also appears as its own tool, so "triage CI run 1234 in acme/web" can call `acme.ci-triage` directly. In Copilot Chat in VS Code, a published workflow can also show its inputs as a form, through MCP Apps.

The server has no editing tools. A workflow is a text file, and Copilot already edits files. The server supplies what Copilot can't know: which steps exist, whether the file is right, and what happened when it ran.

## Building a workflow in chat

A typical exchange, condensed:

1. **You:** "Make a workflow that summarizes a PR's diff and posts it as a comment."
2. **Copilot** calls `find_steps("fetch pull request diff")`, `find_steps("summarize diff")` and `find_steps("post PR comment")`, then `get_step` on each result.
3. **Copilot** writes `pr-summary.yaml` and calls `check_workflow`. It gets back:
   ```text
   unknown_input at /steps/post/with/bdy: your-team/post-comment@1.1.0 has no input 'bdy'; did you mean 'body'?
   missing_input at /steps/post/with: your-team/post-comment@1.1.0 needs input 'pr'
   ```
4. **Copilot** fixes both and checks again: 0 errors.
5. **Copilot** calls `run_workflow` on the file with your inputs, then `get_run` until the run finishes, and shows you the comment's URL.
6. **You** review the file like any other change, and publish it when it's right.

## Saving what an agent did

After an agent step solves a problem, ask Copilot: "Save run run_7f3c2a91 as a workflow." Copilot:
1. reads the run with `get_run`
2. writes a workflow from the agent's calls (guides/write-a-workflow.md)
3. checks it, then reruns it with the original inputs and compares the outputs

You review the result before publishing.

## What you can't do

- **Steps beyond your permissions.** The server checks each workflow against your caller policy. A step that changes outside data, or needs a secret you don't have, fails checking with `effects_not_allowed` or `secret_not_granted` before anything runs.
- **Steps outside the catalog.** A workflow can only use published steps. To add a step, publish one (guides/publish-a-step.md).
