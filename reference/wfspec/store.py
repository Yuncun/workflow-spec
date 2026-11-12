"""A runner's local storage: files by content hash, cached outputs, and run records.

    <root>/files/<sha256 hex>    file contents (SPEC.md, [types.file-store])
    <root>/cache/<key hex>.json  the outputs of a cacheable step call (SPEC.md, section 9.4)
    <root>/runs/<run id>.json    run records (SPEC.md, section 9.10)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path


def _write(path: Path, text: str) -> None:
    """Write a file so that a program reading it sees the old text or the new, never part of one."""
    part = path.with_name(f".{path.name}.{os.getpid()}.part")
    part.write_text(text)
    os.replace(part, path)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        for part in ("files", "cache", "runs"):
            (self.root / part).mkdir(parents=True, exist_ok=True)

    # ---- files --------------------------------------------------------------------------

    def put_bytes(self, data: bytes, media_type: str, name: str | None = None) -> dict:
        """Store `data` and return its file value."""
        hexdigest = hashlib.sha256(data).hexdigest()
        target = self.root / "files" / hexdigest
        if not target.exists():
            tmp = target.with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        value = {"digest": "sha256:" + hexdigest, "mediaType": media_type, "size": len(data)}
        if name:
            value["name"] = name
        return value

    def put_file(self, path: Path, media_type: str, name: str | None = None) -> dict:
        return self.put_bytes(Path(path).read_bytes(), media_type, name or Path(path).name)

    def path_of(self, value: dict) -> Path:
        """Where the content of a file value is stored. Raises KeyError if the store doesn't have it."""
        target = self.root / "files" / value["digest"].removeprefix("sha256:")
        if not target.is_file():
            raise KeyError(f"no stored file for {value['digest']}")
        return target

    def copy_out(self, value: dict, folder: Path, name: str) -> Path:
        """A read-only copy of a stored file, for a step to read."""
        target = Path(folder) / name
        shutil.copyfile(self.path_of(value), target)
        target.chmod(0o444)
        return target

    # ---- cache --------------------------------------------------------------------------

    def cached(self, key: str) -> dict | None:
        path = self.root / "cache" / (key.removeprefix("sha256:") + ".json")
        return json.loads(path.read_text()) if path.is_file() else None

    def cache(self, key: str, outputs: dict) -> None:
        path = self.root / "cache" / (key.removeprefix("sha256:") + ".json")
        _write(path, json.dumps(outputs, ensure_ascii=False))

    # ---- run records ----------------------------------------------------------------------

    def save_run(self, record: dict) -> Path:
        path = self.root / "runs" / f"{record['id']}.json"
        _write(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        return path
