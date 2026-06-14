"""
ACC setup file I/O and diff utilities.

ACC stores setups as JSON under:
  Documents/Assetto Corsa Competizione/Setups/<car>/<track>/<name>.json

Functions
---------
load_setup(path)          → dict
save_setup(data, path)    → None   (atomic write via tmp file)
diff_setups(a, b)         → dict[str, tuple]   dot-notation key → (a_val, b_val)
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def load_setup(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_setup(data: dict, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def diff_setups(a: dict, b: dict) -> dict[str, tuple]:
    """
    Return changed leaf values between two ACC setup dicts.

    Keys are dot-notation paths (e.g. "basicSetup.electronics.tc").
    Lists are indexed: "basicSetup.alignment.camber[0]".
    Only keys whose values differ are included.
    """
    flat_a = _flatten(a)
    flat_b = _flatten(b)
    all_keys = set(flat_a) | set(flat_b)
    return {
        k: (flat_a.get(k), flat_b.get(k))
        for k in sorted(all_keys)
        if flat_a.get(k) != flat_b.get(k)
    }


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _flatten(obj: dict | list, prefix: str = "") -> dict:
    out: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            out.update(_flatten(v, full))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            out.update(_flatten(item, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out
