"""Command line: wfspec check | check-step | lock | as-step | mcp-tool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import check
from .documents import EFFECT_ORDER, Issue, ParseError, agent_tool_issues, load_catalog, load_document, manifest_issues
from .publish import as_step, lock, mcp_tool


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
    p.add_argument("--catalog", type=Path, action="append", default=[], help="folder of step manifests, to check an agent's tools (repeatable)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")

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
    if not found and args.catalog:
        found = agent_tool_issues(manifest, load_catalog(args.catalog))
    issues = [i.to_dict() for i in found]
    if args.json:
        print(json.dumps({"ok": not issues, "errors": issues}, indent=2, ensure_ascii=False))
    else:
        _report(issues, "error")
        print(_count(len(issues), "error"))
    return 1 if issues else 0


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
    if not isinstance(policy, dict) or set(policy) - {"maxEffects", "secrets"}:
        raise ValueError(f"{path}: a policy has only maxEffects and secrets")
    if "maxEffects" in policy and policy["maxEffects"] not in EFFECT_ORDER:
        raise ValueError(f"{path}: maxEffects is one of none, reads, writes")
    if "secrets" in policy and not all(isinstance(s, str) for s in policy["secrets"]):
        raise ValueError(f"{path}: secrets is a list of names")
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
    "lock": _lock,
    "as-step": _as_step,
    "mcp-tool": _mcp_tool,
}

if __name__ == "__main__":
    sys.exit(main())
