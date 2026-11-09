# Step runtimes

Draft, 2026-11-09. **Not normative.** The reference runner follows sections 1 and 2 for `process` and `agent` steps, and the cases in `tests/run/` exercise them. This document becomes a normative companion to [SPEC.md](../SPEC.md) once a second runner, or a platform, confirms it in practice. Until then, a runner may follow it or not.

SPEC.md says what a run must produce. This document says how a runner starts one step, for each runtime kind a manifest can name in `runtime`. SPEC.md, section 5.3 defines the `workflow` kind.

| Kind | Runs | Fields |
|---|---|---|
| `process` | a program started on the runner's machine | `command` |
| `container` | a program in a container image | `image`, `command` |
| `agent` | a model that answers, and may call steps | `model`, `instructions`, `maxTurns` |
| `mcp` | a tool on a remote MCP server | `server`, `tool` |

## 1. Programs: `process` and `container`

Both kinds run a program under one contract. They differ only in how the program starts:
- **`process`:** the runner starts `command` directly, with the manifest's folder as the working directory.
- **`container`:** the runner starts `image`. When `command` is given, it replaces the image's entrypoint, as in Kubernetes. Without it, the image's own entrypoint and arguments run.

The contract:
- **Inputs.** The runner checks the input values and fills in defaults (SPEC.md, [run.input-check]). It then writes them as JSON to the file named by the environment variable `WF_INPUTS`.
  - Wherever `inputSchema` declares a file, at any depth through `properties` and `items`, the value also gets a `path` field: a local, read-only copy of the file.
  - `path` is added after the check and isn't part of the file value.
- **Outputs.** The program writes a JSON object of its outputs to the file named by `WF_OUTPUTS`.
  - Wherever `outputSchema` declares a file, the program writes `{"path": "...", "mediaType": "..."}`. It can leave out `mediaType` when the schema's `x-mediaType` names a single type.
  - A relative `path` is relative to the folder that holds the `WF_OUTPUTS` file, which is the program's work folder. It must lead to a regular file inside that folder without passing through a symbolic link; the runner refuses any other with `invalid_output`, because it reads the file outside any sandbox.
  - The runner stores each file, replaces it with a file value, and then checks the outputs ([run.output-check]). A file that holds the value of any of the run's secrets fails the step instead ([run.secrets-hidden]).
- **Environment.** The program gets only these environment variables: `PATH`, `LANG`, `HOME` and `TMPDIR` (both set to its work folder), `WF_INPUTS`, `WF_OUTPUTS`, `WF_SKIP`, and its secrets. On macOS the system adds `__CF_USER_TEXT_ENCODING` to every program.
- **Skipping.** A step whose manifest has `maySkip` and that has nothing to do writes a reason to the file named by `WF_SKIP` instead of writing outputs ([run.skip]).
- **Secrets.** Each secret named in `secrets` is set as an environment variable with the same name, and no other secret is set ([run.secrets-named]).
- **Exit and logs.** Exit code 0 means success. Any other exit code fails the step with `failed`. Standard output and standard error are the step's logs.
- **Processes.** The program runs in a process group of its own. When it exits or times out, the runner ends every process left in that group.
- **Pinning** ([publish.resolve]). Before computing the step's digest, a publisher writes content digests into the manifest, so the step's digest covers them:
  - for `container`, the image reference becomes `image@sha256:...`
  - for `process`, the runtime object gets `digests`, mapping each file the program needs, by its path relative to the manifest's folder, to the SHA-256 of its bytes, written `sha256:<hex>`

  A runner checks each digest before the step runs, and fails the step with `pinned_mismatch` when a file differs or isn't inside the manifest's folder (SPEC.md, [run.pinned]).

A program can't call steps under this draft (proposals.md, P16).

*Informative.* The reference runner (`reference/wfspec/runtimes.py`) enforces `effects` for programs on macOS with the system sandbox (`sandbox-exec`):
- A `none` step gets no network, and can't read the shared temporary folders (`/private/tmp` and `/private/var/folders`) or other volumes, apart from its work folder.
- Neither a `none` nor a `reads` step may write outside its work folder.
- Neither may read files under `/Users` or the home folder, except its manifest's folder, its work folder, and folders the caller grants with `wfspec run --allow-read FOLDER`. A program installed under a home folder, such as a Python from pyenv or uv, needs its installation granted the same way, and the runner's error names the folder.
- Neither may send signals outside its own process group.
- A step that writes runs without a sandbox, with the person's full access.

The sandbox can't stop a program from reading the environment that another program of the same person started with. So `wfspec run` starts itself again without the secrets given with `--secret`, and receives their values over a pipe. A step can still read the environment of other programs that hold secrets, including a later step that names them, if it leaves a process running outside its process group, which the runner can't find to end. Steps from people you don't trust belong in containers or virtual machines.

Where `sandbox-exec` isn't available, the runner refuses programs that don't write, unless the caller passes `--no-sandbox`. The test case `tests/run/effects/` and `reference/tests/test_isolation.py` check the sandbox.

## 2. Agents: `agent`

- `model` names a model the runner can serve. How the runner maps names to models and providers is its choice.
- `instructions` is the path of a text file inside the manifest's folder, relative to it. Before computing the step's digest, a publisher writes the SHA-256 of the file's bytes into the runtime object as `instructionsDigest` ([publish.resolve]). A runner checks it before the step runs, and fails the step with `pinned_mismatch` when the file differs or isn't inside the manifest's folder ([run.pinned]).
- `maxTurns` limits the number of model responses. When it's left out, the runner chooses the limit.

The runner:
1. **Starts the model.** The instructions become the system prompt, and the input values, as JSON, the first user message. Every file input must be readable by the agent. Whether the runner puts the text in the message, attaches it, or relies on a step in `calls` that reads files is its choice. *Informative:* the reference runner puts the text of `text/*` files in the message, up to 200,000 characters, with `textCut: true` when it cuts one short. It gives other files only as their digest, media type and size, so an agent step that needs one must call a step that reads it.
2. **Offers tools:**
   - each step in `calls`, under its MCP tool name ([mcp.md](mcp.md), section 1), with its `inputSchema` as the parameters
   - `finish`, whose parameters are the step's `outputSchema`
   - `skip`, with one text parameter `reason`, when the manifest has `maySkip`
3. **Runs each tool call as a step** ([run.calls-listed]) and returns the step's outputs, or its error, as the tool result. A response can contain several calls; the runner runs all of them in order and records each one ([run.calls-recorded]).
4. **Finishes** when the model calls `finish` or `skip`.
   - The runner checks the arguments of `finish` against `outputSchema`. When they don't fit, it returns the problems as the result of that call, and gives the model another turn.
   - It allows a limited number of such retries; 2 is a sensible default.
   - When the retries or `maxTurns` run out, the step fails with `invalid_output` or `failed`.
5. **Handles models without tool calls.** A runner MAY run an agent step that has no `calls` by asking for one typed answer instead.
   - The answer's JSON Schema wraps the outputs as `{"outputs": <outputSchema>}`.
   - With `maySkip`, the answer may instead be `{"skip": "<reason>"}`.
   - Wrapping the outputs keeps an output named `skip` unambiguous.

*Informative.* On 2026-11-08, Qwen 3.8 27B, served locally by LM Studio, ran an agent step with two steps in `calls`:
- It finished in 4 turns and 9.3 seconds.
- It read two files in a single response.
- Its first `finish` broke a `maxLength`, and it fixed the answer when the problem came back.

LM Studio doesn't hold tool-call arguments to their schema, which is why step 4 checks them.

## 3. Remote tools: `mcp`

- `server` is the URL of an MCP server, and `tool` is the name of a tool on it.
- The runner calls the tool with the input values as its arguments. It takes the `structuredContent` of the tool's result as the outputs.
- A result with `isError` fails the step with `failed`.
- The server's code can't be pinned by content hash, so the manifest's author vouches for `effects` and `deterministic`. A runner should treat such a step as not deterministic unless it trusts the server's owner.

## 4. Open questions

- Can a program step call steps? It would need an interface from the runner, which brings in a service API (proposals.md, P16).
- What resource limits does a step declare (proposals.md, P4)?
- How does a runner report a step's progress while it runs?
