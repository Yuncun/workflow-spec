"""How the reference runner starts one step, for the runtime kinds in drafts/runtime.md:
`process` (a program on this machine) and `agent` (a model behind an OpenAI-compatible
chat completions API, such as LM Studio's).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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


def _quoted(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandbox_profile(effects: str, work: Path, readable: list[Path]) -> str:
    """A macOS sandbox profile for a program that doesn't write outside data:
    - no reading files under /Users or the home folder, except the folders in `readable`
    - for `none`, no reading the shared temporary folders or other volumes either, and no network
    - no writing outside the work folder
    - no signals, except to itself, its children and its process group, which is the step's own
    The sandbox can't stop a program from reading the environment another program of the same
    user started with, so the runner keeps secrets out of its own (cli.py, _secrets)."""
    private = {Path("/Users"), Path.home().resolve()}
    if effects == "none":  # it touches no outside data at all (SPEC.md, section 5.2)
        private |= {Path("/private/tmp"), Path("/private/var/folders"), Path("/Volumes")}
    rules = ["(version 1)", "(allow default)"]
    rules += [f"(deny file-read-data (subpath {_quoted(h)}))" for h in sorted(private)]
    rules += [f"(allow file-read-data (subpath {_quoted(r)}))" for r in readable]
    rules += ["(deny file-write*)", f'(allow file-write* (subpath {_quoted(work)}) (subpath "/dev"))']
    rules += ["(deny signal)"] + [f"(allow signal (target {t}))" for t in ("self", "children", "pgrp")]
    if effects == "none":
        rules.append("(deny network*)")
    return "\n".join(rules)


def inside(path: Path, folder: Path) -> bool:
    """Whether `path`, with symbolic links followed, is inside `folder`."""
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def check_pinned(folder: Path, digests: dict[str, str]) -> None:
    """Fail with pinned_mismatch when a file a manifest pins no longer has its digest (SPEC.md, [run.pinned])."""
    for name, expected in digests.items():
        path = folder / name
        if not inside(path, folder) or not path.is_file():
            raise StepFailed("pinned_mismatch", f"the pinned file {name} isn't in the step's folder")
        if file_digest(path) != expected:
            raise StepFailed("pinned_mismatch", f"{name} has changed since the step was published")


def home_installation(command: list[str], path: str) -> Path | None:
    """The installation folder of the program a command starts, when it's under a home folder, so
    an error can name the folder to grant. The runner never grants it by itself: a program in a
    folder such as ~/bin would make that the whole home folder."""
    found = shutil.which(command[0], path=path)
    if not found:
        return None
    real = Path(found).resolve()
    return real.parent.parent if inside(real, Path("/Users")) or inside(real, Path.home()) else None


def read_inside(folder_fd: int, path: str) -> bytes | None:
    """The bytes of the regular file at `path`, relative to the folder open as `folder_fd`, read
    without following a symbolic link anywhere on the way; None when there's no such file. Reading
    through the folder's handle keeps a program from pointing the runner at another file, even by
    changing its folder while the runner reads."""
    parts = PurePosixPath(path).parts
    if not parts or parts[0] == "/" or ".." in parts:
        raise ValueError("isn't a path inside the work folder")
    opened: list[int] = []
    try:
        at = folder_fd
        for part in parts[:-1]:
            at = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)
            opened.append(at)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=at)
        opened.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("isn't a regular file")
        with open(fd, "rb", closefd=False) as f:
            return f.read()
    except FileNotFoundError:
        return None
    except OSError as exc:  # a symbolic link on the way, or a file where a folder should be
        raise ValueError(f"can't be read without following a link ({exc.strerror})") from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _end_group(proc: subprocess.Popen) -> None:
    """End every process left in a step's process group."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass  # the group has ended already


def _leftover_output(proc: subprocess.Popen) -> bytes:
    """What a timed-out program wrote, once its process group has ended."""
    try:
        return b"".join(proc.communicate(timeout=5))
    except subprocess.TimeoutExpired:  # a process that left the group still holds the output open
        proc.kill()
        for pipe in (proc.stdout, proc.stderr):
            pipe.close()
        proc.wait()
        return b""


def run_process(manifest: dict, folder: Path, inputs: dict, secrets: dict[str, str], store: Store,
                timeout: float, sandbox: bool, readable: list[Path], secret_values: tuple[str, ...] = ()) -> Outcome:
    """drafts/runtime.md, section 1: inputs in WF_INPUTS, outputs in WF_OUTPUTS, a reason in WF_SKIP.
    `readable` lists folders the caller lets programs read, besides their own folder and work folder.
    `secret_values` are the values of all the run's secrets, which no output file may hold."""
    runtime = manifest["runtime"]["process"]
    command = runtime["command"]
    effects = manifest["effects"]
    if sandbox and effects != "writes" and not SANDBOX:
        raise StepFailed("failed", "this runner can't enforce effects for programs here, because sandbox-exec isn't available; "
                                   "run with --no-sandbox to run the step anyway")
    check_pinned(folder, runtime.get("digests", {}))
    with tempfile.TemporaryDirectory(prefix="wfspec-step-", ignore_cleanup_errors=True) as tmp:
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
        if sandbox and effects != "writes":
            allowed = [folder.resolve(), work, *(Path(r).resolve() for r in readable)]
            argv = [SANDBOX, "-p", sandbox_profile(effects, work, allowed), *argv]
        work_fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)  # the runner reads results only through this
        try:
            return _finish_process(manifest, argv, folder, env, work, work_fd, store, timeout,
                                   sandbox and effects != "writes", secret_values)
        finally:
            os.close(work_fd)


def _finish_process(manifest: dict, argv: list[str], folder: Path, env: dict, work: Path, work_fd: int,
                    store: Store, timeout: float, sandboxed: bool, secret_values: tuple[str, ...]) -> Outcome:
    """Run a program in its own process group, end whatever it leaves running, and read its results."""
    command = manifest["runtime"]["process"]["command"]
    try:
        proc = subprocess.Popen(argv, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise StepFailed("failed", f"couldn't start {command[0]}: {exc}")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _end_group(proc)
        raise StepFailed("timeout", f"the step, or a process it left running, ran longer than {timeout:g} seconds",
                         _leftover_output(proc))
    finally:
        _end_group(proc)  # what a program leaves running ends with its step
    logs = stdout + stderr
    if proc.returncode != 0:
        hint = ""
        if sandboxed:
            home = home_installation(command, env["PATH"])
            hint = (f"; it ran in a sandbox, and {command[0]} is installed under a home folder, so grant its "
                    f"installation with --allow-read {home}" if home else
                    "; it ran in a sandbox, so if it needs a folder under your home folder, grant it with --allow-read")
        raise StepFailed("failed", f"the program exited with code {proc.returncode}{hint}", logs)

    def read(path: str, what: str) -> bytes | None:
        try:
            return read_inside(work_fd, path)
        except ValueError as exc:  # the runner reads outside any sandbox, so only the step's own files
            raise StepFailed("invalid_output", f"{what} {exc}", logs)

    skip = read("skip.txt", "WF_SKIP")
    if skip is not None:
        if not manifest.get("maySkip"):
            raise StepFailed("failed", "the step skipped, but its manifest doesn't set maySkip", logs)
        return Outcome(skipped=skip.decode(errors="replace").strip() or "nothing to do", logs=logs)
    result = read("outputs.json", "WF_OUTPUTS")
    if result is None:
        raise StepFailed("invalid_output", "the program wrote no outputs to WF_OUTPUTS", logs)
    try:
        outputs = json.loads(result)
    except ValueError as exc:
        raise StepFailed("invalid_output", f"WF_OUTPUTS isn't JSON: {exc}", logs)

    def stored(value: dict, schema: dict):
        if "path" not in value:
            return value
        given = value["path"]
        if not isinstance(given, str):
            raise StepFailed("invalid_output", "an output file's path isn't text", logs)
        relative = PurePosixPath(given)  # relative to the folder of WF_OUTPUTS
        if relative.is_absolute():
            if not relative.is_relative_to(str(work)):
                raise StepFailed("invalid_output", f"the output file {given} isn't inside the step's work folder", logs)
            relative = relative.relative_to(str(work))
        data = read(str(relative), f"the output file {given}")
        if data is None:
            raise StepFailed("invalid_output", f"the output file {given} doesn't exist", logs)
        if any(secret.encode() in data for secret in secret_values):  # SPEC.md, [run.secrets-hidden]
            raise StepFailed("failed", f"the output file {given} contains a secret's value", logs)
        declared = schema.get("x-mediaType", "")
        media_type = value.get("mediaType") or (declared if declared and "*" not in declared else None)
        if not media_type:
            raise StepFailed("invalid_output", f"the output file {given} has no mediaType", logs)
        return store.put_bytes(data, media_type, relative.name)

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
        return {**value, "text": content[:TEXT_LIMIT], **({"textCut": True} if len(content) > TEXT_LIMIT else {})}

    return map_files(inputs, schema, text)


def run_agent(manifest: dict, folder: Path, inputs: dict, store: Store, model: Model,
              lookup: Callable[[str], dict], call: Callable[[str, dict], dict]) -> Outcome:
    """drafts/runtime.md, section 2. `lookup(ref)` gives a called step's manifest; `call(ref, inputs)` runs it
    as a step and returns the tool result."""
    spec = manifest["runtime"]["agent"]
    path = folder / spec["instructions"]
    code = "pinned_mismatch" if "instructionsDigest" in spec else "failed"  # pinned content: SPEC.md, [run.pinned]
    if not inside(path, folder):
        raise StepFailed(code, f"the instructions file {spec['instructions']} isn't in the step's folder")
    if not path.is_file():
        raise StepFailed(code, f"the instructions file {spec['instructions']} doesn't exist")
    if "instructionsDigest" in spec:
        check_pinned(folder, {spec["instructions"]: spec["instructionsDigest"]})
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
