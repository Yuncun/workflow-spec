"""Command line: wfspec check | check-step | run | lock | as-step | mcp-tool."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path

from .check import check
from .documents import (
    Issue,
    ParseError,
    calls_issues,
    load_catalog,
    schema_issues,
    load_document,
    manifest_issues,
    workflow_runtime_issues,
)
from .publish import as_step, lock, mcp_tool
from .run import Options, Runner, compare_dry_run
from .runtimes import Model
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wfspec", description="Reference tools for the workflow spec (see SPEC.md).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="check a workflow against a catalog of step manifests")
    p.add_argument("workflow", type=Path)
    p.add_argument("--catalog", type=Path, action="append", required=True, help="folder of step manifests (repeatable)")
    p.add_argument("--policy", type=Path, help="caller policy file with maxEffects and secrets")
    p.add_argument("--json", action="store_true", help="print the result as JSON")

    p = sub.add_parser("check-step", help="check one step manifest")
    p.add_argument("manifest", type=Path)
    p.add_argument("--catalog", type=Path, action="append", default=[], help="folder of step manifests, to check the steps it calls (repeatable)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")

    p = sub.add_parser("run", help="run a workflow with the reference runner")
    p.add_argument("workflow", type=Path)
    p.add_argument("--catalog", type=Path, action="append", required=True, help="folder of step manifests (repeatable)")
    p.add_argument("--input", action="append", default=[], metavar="NAME=VALUE", help="a workflow input; VALUE is read as JSON when it parses, otherwise as text")
    p.add_argument("--inputs", type=Path, help="a JSON or YAML file of workflow inputs")
    p.add_argument("--file", action="append", default=[], metavar="NAME=PATH[:TYPE]", help="a file input, stored by its content hash")
    p.add_argument("--policy", type=Path, help="caller policy file with maxEffects and secrets")
    p.add_argument("--secret", action="append", default=[], metavar="NAME", help="give the run the environment variable NAME as a secret")
    p.add_argument("--store", type=Path, default=Path(".wfspec"), help="where files, cached outputs and run records go")
    p.add_argument("--retries", type=int, default=0, help="automatic retries for a failed step that doesn't write")
    p.add_argument("--timeout", type=float, default=300, help="seconds a program may run")
    p.add_argument("--model-url", default="http://127.0.0.1:1234/v1", help="OpenAI-compatible API for agent steps")
    p.add_argument("--model", help="use this model for every agent step")
    p.add_argument("--no-sandbox", action="store_true", help="don't enforce effects with the macOS sandbox")
    p.add_argument("--allow-read", type=Path, action="append", default=[], metavar="FOLDER",
                   help="let programs that don't write read this folder under your home folder (repeatable)")
    p.add_argument("--lock", type=Path, help="the workflow's lock file; the run is refused if it no longer matches")
    p.add_argument("--dry-run-of", type=Path, metavar="RUN_RECORD", help="replay the outputs of steps that write from this run, and compare")
    p.add_argument("--json", action="store_true", help="print the run record as JSON")

    for name, text in (
        ("lock", "print the lock file for a workflow"),
        ("as-step", "print the manifest a workflow gets when it is published"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("workflow", type=Path)
        p.add_argument("--catalog", type=Path, action="append", required=True, help="folder of step manifests (repeatable)")

    p = sub.add_parser("mcp-tool", help="print the MCP tool definition for a step manifest")
    p.add_argument("manifest", type=Path)

    args = parser.parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (ParseError, ValueError) as exc:
        print(f"wfspec: {exc}", file=sys.stderr)
        return 1


def _check(args) -> int:
    catalog = load_catalog(args.catalog)
    policy = _policy(args.policy) if args.policy else None
    order: list[str] = []
    try:
        result = check(load_document(args.workflow), catalog, policy)
        out, order = result.to_dict(), result.order
    except ParseError as exc:
        out = {"ok": False, "errors": [Issue("parse_error", "", str(exc)).to_dict()], "warnings": []}
    if catalog.problems:
        out["catalog"] = [i.to_dict() for i in catalog.problems]
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        _report(out["errors"], "error")
        _report(out["warnings"], "warning")
        _report(out.get("catalog", []), "catalog")
        print(_count(len(out["errors"]), "error") + ", " + _count(len(out["warnings"]), "warning"))
        if out["ok"] and order:
            print("a run would do: " + " -> ".join(order))
    return 0 if out["ok"] else 1


def _check_step(args) -> int:
    manifest = load_document(args.manifest)
    found = manifest_issues(manifest)
    if not found:
        found = workflow_runtime_issues(manifest, str(args.manifest))
    if not found and args.catalog:
        found = calls_issues(manifest, load_catalog(args.catalog))
    issues = [i.to_dict() for i in found]
    if args.json:
        print(json.dumps({"ok": not issues, "errors": issues}, indent=2, ensure_ascii=False))
    else:
        _report(issues, "error")
        print(_count(len(issues), "error"))
    return 1 if issues else 0


def _run(args) -> int:
    catalog = load_catalog(args.catalog)
    store = Store(args.store)
    try:
        workflow = load_document(args.workflow)
    except ParseError as exc:  # SPEC.md, [run.check-first]: a run that never starts still gets a record
        record = Runner(catalog, Options(store=store)).unreadable(Path(args.workflow).read_bytes(), str(exc))
        _print_run(record, store)
        return 1
    declared = (workflow.get("inputSchema") or {}).get("properties", {}) if isinstance(workflow, dict) else {}
    inputs = dict(load_document(args.inputs)) if args.inputs else {}
    for item in args.input:
        name, _, raw = item.partition("=")
        inputs[name] = _input_value(raw, declared.get(name))
    for item in args.file:
        name, _, rest = item.partition("=")
        path, media_type = rest, None
        if ":" in rest and "/" in rest.rsplit(":", 1)[1]:
            path, media_type = rest.rsplit(":", 1)
        media_type = media_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if not Path(path).is_file():
            raise ValueError(f"--file {name}: there is no file at {path}")
        inputs[name] = store.put_file(Path(path), media_type)
    missing = [s for s in args.secret if s not in os.environ]
    if missing:
        raise ValueError(f"these secrets aren't set in the environment: {', '.join(missing)}")
    replay = load_document(args.dry_run_of) if args.dry_run_of else None
    options = Options(
        store=store,
        policy=_policy(args.policy) if args.policy else None,
        secrets={s: os.environ[s] for s in args.secret},
        retries=args.retries,
        timeout=args.timeout,
        model=Model(url=args.model_url, name=args.model),
        sandbox=not args.no_sandbox,
        replay=replay,
        lock=load_document(args.lock) if args.lock else None,
        readable=args.allow_read,
    )
    record = Runner(catalog, options).run(workflow, inputs)
    differences = compare_dry_run(replay, record, catalog) if replay else []
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        _print_run(record, store)
        if replay:
            print("dry run: " + ("the outputs match the original run" if not differences else "; ".join(differences)))
    return 0 if record["status"] == "succeeded" and not differences else 1


def _input_value(raw: str, schema) -> object:
    """A value given on the command line: text when the input is declared as text, otherwise JSON
    when it parses as JSON, otherwise text. A commit hash such as 584e862 stays text."""
    schema = schema if isinstance(schema, dict) else {}
    texts = schema.get("type") == "string" or (schema.get("enum") and all(isinstance(v, str) for v in schema["enum"]))
    if texts:
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _print_run(record: dict, store: Store) -> None:
    for sid, entry in record["steps"].items():
        notes = []
        if entry.get("cacheHit"):
            notes.append("from the cache")
        if entry.get("attempts", 1) > 1:
            notes.append(f"{entry['attempts']} attempts")
        if entry.get("calls"):
            notes.append("called " + ", ".join(c["uses"] for c in entry["calls"]))
        if entry.get("reason"):
            notes.append(entry["reason"])
        if entry.get("error"):
            notes.append(f"{entry['error']['code']}: {entry['error']['message']}")
        print(f"  {sid:<16} {entry['status']:<10} {'; '.join(notes)}")
    if record.get("error"):
        print(f"run failed: {record['error']['code']}: {record['error']['message']}")
    if record.get("outputs"):
        print("outputs:")
        for name, value in record["outputs"].items():
            shown = f"file {value.get('name', '')} {value['digest'][:19]}" if isinstance(value, dict) and "digest" in value else json.dumps(value, ensure_ascii=False)
            print(f"  {name}: {shown}")
    print(f"{record['status']}; run record: {store.root / 'runs' / (record['id'] + '.json')}")


def _lock(args) -> int:
    print(json.dumps(lock(load_document(args.workflow), load_catalog(args.catalog)), indent=2))
    return 0


def _as_step(args) -> int:
    manifest = as_step(load_document(args.workflow), load_catalog(args.catalog), str(args.workflow))
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def _mcp_tool(args) -> int:
    manifest = load_document(args.manifest)
    issues = manifest_issues(manifest)
    if issues:
        _report([i.to_dict() for i in issues], "error")
        return 1
    print(json.dumps(mcp_tool(manifest), indent=2, ensure_ascii=False))
    return 0


def _policy(path: Path) -> dict:
    policy = load_document(path)
    problems = schema_issues(policy, "urn:wfspec:schema:policy", "invalid_policy")  # SPEC.md, [check.policy]
    if problems:
        raise ValueError(f"{path}: " + "; ".join(f"{p.path or '/'}: {p.message}" for p in problems))
    return policy


def _report(issues: list[dict], kind: str) -> None:
    for issue in issues:
        where = (issue["file"] + " ") if "file" in issue else ""
        print(f"{kind}: {issue['code']} at {where}{issue['path'] or '/'}")
        print(f"    {issue['message']}")


def _count(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


_COMMANDS = {
    "check": _check,
    "check-step": _check_step,
    "run": _run,
    "lock": _lock,
    "as-step": _as_step,
    "mcp-tool": _mcp_tool,
}

if __name__ == "__main__":
    sys.exit(main())
