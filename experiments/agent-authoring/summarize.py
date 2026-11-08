"""Summarize each run of a round: checks used, error codes per attempt, final warnings.

usage: python summarize.py <round folder>   (a folder holding runs/)
"""
import json
import sys
from pathlib import Path

runs = Path(sys.argv[1]).resolve() / "runs"
for run in sorted(p for p in runs.iterdir() if p.is_dir()):
    attempts = sorted(run.glob("attempt-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    history = []
    for attempt in attempts:
        result = json.loads(attempt.read_text())
        history.append("ok" if result["ok"] else "+".join(sorted(e["code"] for e in result["errors"])))
    final = json.loads(attempts[-1].read_text()) if attempts else None
    warnings = sorted({w["code"] for w in final["warnings"]}) if final else []
    print(f"{run.name:28} checks={len(attempts)}  {' -> '.join(history) or '(none)'}  warnings={','.join(warnings) or '-'}")
