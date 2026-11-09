"""Small programs for the runner's test cases: `python3 programs/step.py <behaviour>`.

Each reads its inputs from WF_INPUTS and writes outputs to WF_OUTPUTS, as drafts/runtime.md, section 1 says.
"""
import json
import os
import secrets
import socket
import sys
from pathlib import Path

inputs = json.loads(Path(os.environ["WF_INPUTS"]).read_text())


def give(outputs: dict) -> None:
    Path(os.environ["WF_OUTPUTS"]).write_text(json.dumps(outputs))


def bump(counter: str) -> int:
    path = Path(counter)
    n = int(path.read_text()) + 1 if path.exists() else 1
    path.write_text(str(n))
    return n


behaviour = sys.argv[1]
if behaviour == "echo":
    give({"text": inputs["text"]})
elif behaviour == "upper":
    give({"text": inputs["text"].upper()})
elif behaviour == "greet":
    give({"greeting": f"hello {inputs['name']}"})
elif behaviour == "maybe-name":
    give({"name": "ada"} if inputs["give"] else {})
elif behaviour == "random":
    give({"token": secrets.token_hex(8)})
elif behaviour == "fail":
    print("failing on purpose")
    sys.exit(3)
elif behaviour == "bad-output":
    give({"count": "three"})
elif behaviour == "first-item":
    if not inputs["items"]:
        Path(os.environ["WF_SKIP"]).write_text("the list is empty")
    else:
        give({"item": inputs["items"][0]})
elif behaviour == "save-text":
    target = Path(inputs["folder"]) / inputs["name"]
    target.write_text(inputs["text"])
    give({"path": str(target)})
elif behaviour == "show-secrets":
    print("TOKEN_A is", os.environ.get("TOKEN_A"))
    give({"a_set": "TOKEN_A" in os.environ, "b_set": "TOKEN_B" in os.environ})
elif behaviour in ("flaky", "flaky-write"):
    if bump(inputs["counter"]) == 1:
        print("first attempt fails")
        sys.exit(1)
    give({"done": True})
elif behaviour == "try-network":
    try:
        socket.create_connection(("127.0.0.1", inputs["port"]), timeout=2).close()
        give({"connected": True})
    except OSError:
        give({"connected": False})
elif behaviour == "try-write":
    try:
        Path(inputs["path"]).write_text("x")
        give({"written": True})
    except OSError:
        give({"written": False})
elif behaviour == "make-file":
    (Path(os.environ["TMPDIR"]) / "out.txt").write_text(inputs["text"])
    give({"file": {"path": "out.txt"}})
elif behaviour == "read-file":
    give({"text": Path(inputs["file"]["path"]).read_text()})
elif behaviour == "try-read":
    try:
        Path(inputs["path"]).read_bytes()
        give({"read": True})
    except OSError:
        give({"read": False})
elif behaviour == "escape-output":
    link = Path(os.environ["TMPDIR"]) / "link.txt"
    link.symlink_to("/etc/hosts")
    give({"file": {"path": "link.txt", "mediaType": "text/plain"}})
elif behaviour == "skip-with-secret":
    Path(os.environ["WF_SKIP"]).write_text("nothing to do; token=" + os.environ["TOKEN_A"])
else:
    sys.exit(f"unknown behaviour {behaviour}")
