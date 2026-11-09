"""How the reference runner starts one step, for the runtime kinds in drafts/runtime.md:
`process` (a program on this machine) and `agent` (a model behind an OpenAI-compatible
chat completions API, such as LM Studio's).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .documents import FILE_REF, mcp_name, value_problem
from .publish import _inline_files
from .store import Store

SANDBOX = shutil.which("sandbox-exec")  # macOS; elsewhere the runner can't enforce effects for programs
TEXT_LIMIT = 200_000  # characters of a text file an agent gets inline


class StepFailed(Exception):
    """A step failed. `code` is one of the run-time error codes (SPEC.md, [run.error-codes])."""

    def __init__(self, code: str, message: str, logs: bytes = b""):
        super().__init__(message)
        self.code, self.message, self.logs = code, message, logs


@dataclass
class Outcome:
    """What a step gave back: outputs, or a reason it skipped (SPEC.md, [run.skip])."""

    outputs: dict | None = None
    skipped: str | None = None
    logs: bytes = b""


def map_files(value, schema, fn: Callable[[dict, dict], object]):
    """Apply `fn(value, schema)` to every value that `schema` declares a file, at any depth through
    properties and items."""
    if not isinstance(schema, dict):
        return value
    if schema.get("$ref") == FILE_REF:
        return fn(value, schema) if isinstance(value, dict) else value
    if isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: map_files(v, props.get(k), fn) for k, v in value.items()}
    if isinstance(value, list):
        return [map_files(v, schema.get("items"), fn) for v in value]
    return value


# ---- process --------------------------------------------------------------------------------


def sandbox_profile(effects: str, work: Path) -> str:
    """A macOS sandbox profile: no writes outside the work folder, and for `none` no network either."""
    rules = ["(version 1)", "(allow default)", "(deny file-write*)",
             f'(allow file-write* (subpath "{work}") (subpath "/dev"))']
    if effects == "none":
        rules.append("(deny network*)")
    return "\n".join(rules)


def run_process(manifest: dict, folder: Path, inputs: dict, secrets: dict[str, str], store: Store,
                timeout: float, sandbox: bool) -> Outcome:
    """drafts/runtime.md, section 1: inputs in WF_INPUTS, outputs in WF_OUTPUTS, a reason in WF_SKIP."""
    command = manifest["runtime"]["process"]["command"]
    with tempfile.TemporaryDirectory(prefix="wfspec-step-") as tmp:
        work = Path(tmp).resolve()
        counter = iter(range(1_000_000))

        def with_path(value: dict, _schema: dict) -> dict:
            name = value.get("name") or f"input-{next(counter)}"
            return {**value, "path": str(store.copy_out(value, work, f"{next(counter)}-{Path(name).name}"))}

        payload = map_files(inputs, manifest["inputSchema"], with_path)
        (work / "inputs.json").write_text(json.dumps(payload, ensure_ascii=False))
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(work),
            "TMPDIR": str(work),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "WF_INPUTS": str(work / "inputs.json"),
            "WF_OUTPUTS": str(work / "outputs.json"),
            "WF_SKIP": str(work / "skip.txt"),
            **secrets,  # only the secrets the manifest names (SPEC.md, [run.secrets-named])
        }
        argv = list(command)
        if sandbox and SANDBOX and manifest["effects"] != "writes":
            argv = [SANDBOX, "-p", sandbox_profile(manifest["effects"], work), *argv]
        try:
            proc = subprocess.run(argv, cwd=folder, env=env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise StepFailed("timeout", f"the step ran longer than {timeout:g} seconds", (exc.stdout or b"") + (exc.stderr or b""))
        except OSError as exc:
            raise StepFailed("failed", f"couldn't start {command[0]}: {exc}")
        logs = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise StepFailed("failed", f"the program exited with code {proc.returncode}", logs)
        skip = work / "skip.txt"
        if skip.exists():
            if not manifest.get("maySkip"):
                raise StepFailed("failed", "the step skipped, but its manifest doesn't set maySkip", logs)
            return Outcome(skipped=skip.read_text().strip() or "nothing to do", logs=logs)
        result = work / "outputs.json"
        if not result.exists():
            raise StepFailed("invalid_output", "the program wrote no outputs to WF_OUTPUTS", logs)
        try:
            outputs = json.loads(result.read_text())
        except json.JSONDecodeError as exc:
            raise StepFailed("invalid_output", f"WF_OUTPUTS isn't JSON: {exc}", logs)

        def stored(value: dict, schema: dict):
            if "path" not in value:
                return value
            path = Path(value["path"])
            path = path if path.is_absolute() else work / path  # relative to the folder of WF_OUTPUTS
            if not path.is_file():
                raise StepFailed("invalid_output", f"the output file {value['path']} doesn't exist", logs)
            declared = schema.get("x-mediaType", "")
            media_type = value.get("mediaType") or (declared if declared and "*" not in declared else None)
            if not media_type:
                raise StepFailed("invalid_output", f"the output file {value['path']} has no mediaType", logs)
            return store.put_file(path, media_type, path.name)

        outputs = map_files(outputs, manifest["outputSchema"], stored)
        return Outcome(outputs=outputs, logs=logs)


# ---- agent ----------------------------------------------------------------------------------


@dataclass
class Model:
    """A model behind an OpenAI-compatible chat completions API."""

    url: str = "http://127.0.0.1:1234/v1"
    name: str | None = None  # overrides the manifest's `model` when set
    finish_retries: int = 2  # how many times a final answer that doesn't fit may be retried

    def chat(self, body: dict) -> dict:
        request = urllib.request.Request(
            self.url.rstrip("/") + "/chat/completions",
            json.dumps({"temperature": 0, **body}).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                reply = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise StepFailed("failed", f"the model at {self.url} didn't answer: {exc}")
        if not reply.get("choices"):
            raise StepFailed("failed", f"the model's reply has no choices: {json.dumps(reply)[:300]}")
        return reply["choices"][0]["message"]


def readable(inputs: dict, schema: dict, store: Store) -> dict:
    """Input values for an agent, with the text of each text file inline (drafts/runtime.md, section 2)."""

    def text(value: dict, _schema: dict):
        if not str(value.get("mediaType", "")).startswith("text/"):
            return value
        try:
            content = store.path_of(value).read_text(errors="replace")
        except KeyError:
            return value
        return {**value, "text": content[:TEXT_LIMIT]}

    return map_files(inputs, schema, text)


def run_agent(manifest: dict, folder: Path, inputs: dict, store: Store, model: Model,
              lookup: Callable[[str], dict], call: Callable[[str, dict], dict]) -> Outcome:
    """drafts/runtime.md, section 2. `lookup(ref)` gives a called step's manifest; `call(ref, inputs)` runs it
    as a step and returns the tool result."""
    spec = manifest["runtime"]["agent"]
    path = folder / spec["instructions"]
    if not path.is_file():
        raise StepFailed("failed", f"the instructions file {spec['instructions']} doesn't exist")
    out_schema = manifest["outputSchema"]
    messages = [
        {"role": "system", "content": path.read_text()},
        {"role": "user", "content": json.dumps(readable(inputs, manifest["inputSchema"], store), ensure_ascii=False)},
    ]
    name = model.name or spec["model"]
    calls = manifest.get("calls", [])

    if not calls and not manifest.get("maySkip"):
        # One typed answer, with the outputs wrapped so no output name can clash with the wrapper.
        wrapper = {"type": "object", "properties": {"outputs": _inline_files(out_schema)}, "required": ["outputs"], "additionalProperties": False}
        problem = "no answer"
        for _ in range(model.finish_retries + 1):
            reply = model.chat({"model": name, "messages": messages,
                                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": wrapper}}})
            content = reply.get("content") or ""
            try:
                outputs = json.loads(content).get("outputs")
            except (json.JSONDecodeError, AttributeError):
                outputs, problem = None, "the answer isn't JSON"
            else:
                problem = value_problem(out_schema, outputs) if isinstance(outputs, dict) else "the answer has no outputs object"
            if problem is None:
                return Outcome(outputs=outputs)
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"That answer doesn't fit the output schema: {problem}. Answer again."}]
        raise StepFailed("invalid_output", f"the agent's answer doesn't fit outputSchema: {problem}")

    offered = {mcp_name(*ref.split("@")): ref for ref in calls}
    tools = [_tool(mcp, lookup(ref)["description"], _inline_files(lookup(ref)["inputSchema"])) for mcp, ref in offered.items()]
    tools.append(_tool("finish", "Give your final answer. Call this once, last.", _inline_files(out_schema)))
    if manifest.get("maySkip"):
        tools.append(_tool("skip", "Finish without outputs because there is nothing to do.",
                           {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}))
    retries = 0
    for _ in range(spec.get("maxTurns", 20)):
        reply = model.chat({"model": name, "messages": messages, "tools": tools})
        tool_calls = reply.get("tool_calls") or []
        if not tool_calls:
            messages += [{"role": "assistant", "content": reply.get("content") or ""},
                         {"role": "user", "content": "Call one of your tools, or call finish with your answer."}]
            continue
        messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": tool_calls})
        outcome = None
        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                result = {"error": "the arguments aren't JSON"}
            else:
                if fn.get("name") == "finish":
                    problem = value_problem(out_schema, args)
                    if problem is None:
                        outcome, result = Outcome(outputs=args), {"accepted": True}
                    else:
                        retries += 1
                        if retries > model.finish_retries:
                            raise StepFailed("invalid_output", f"the agent's answer doesn't fit outputSchema: {problem}")
                        result = {"accepted": False, "problem": problem}
                elif fn.get("name") == "skip" and manifest.get("maySkip"):
                    outcome, result = Outcome(skipped=str(args.get("reason") or "nothing to do")), {"accepted": True}
                elif fn.get("name") in offered:
                    result = call(offered[fn["name"]], args)
                else:
                    result = {"error": f"there is no tool named {fn.get('name')!r}"}
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": json.dumps(result, ensure_ascii=False)})
        if outcome is not None:
            return outcome
    raise StepFailed("failed", f"the agent didn't finish within {spec.get('maxTurns', 20)} turns")


def _tool(name: str, description: str, parameters: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}
