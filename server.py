# SPDX-License-Identifier: Apache-2.0 OR MIT
"""FBX in, OpenUSD out, through ufbx. RFD 0036's interface, RFD 0053's format.

The probe runs before any conversion and its findings travel with the result. That ordering is
the point: a caller can tell what the file said from what the conversion was asked to do, and
a converter that reports only its own settings cannot be checked.
"""

import base64
import hashlib
import json
import pathlib
import subprocess
import tempfile
import urllib.request

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

# ufbx's handlings, named the way the caller thinks about them rather than the way the enum
# spells them. Godot picks between the first two with `allow_geometry_helper_nodes`; see
# modules/fbx/fbx_document.cpp.
PIVOT_MODES = {
    "helper_nodes": "UFBX_GEOMETRY_TRANSFORM_HANDLING_HELPER_NODES",
    "modify_geometry": "UFBX_GEOMETRY_TRANSFORM_HANDLING_MODIFY_GEOMETRY_NO_FALLBACK",
    "preserve": "UFBX_GEOMETRY_TRANSFORM_HANDLING_PRESERVE",
}


class Request(BaseModel):
    fbx: str
    pivots: str = "helper_nodes"
    up_axis: str = "z"
    meters_per_unit: float = 1.0
    resample_fps: float = 0.0
    stub: bool = False


def fetch(src: str, dst: pathlib.Path) -> pathlib.Path:
    """A path, a URL, or base64. LFS-backed URLs work as plain HTTPS through
    media.githubusercontent.com, so no LFS client is needed and none is used."""
    if src.startswith(("http://", "https://")):
        urllib.request.urlretrieve(src, dst)
    elif pathlib.Path(src).exists():
        dst.write_bytes(pathlib.Path(src).read_bytes())
    else:
        dst.write_bytes(base64.b64decode(src))
    return dst


def probe(path: pathlib.Path) -> dict:
    """What the file says about itself. Parsed, never assumed."""
    out = subprocess.run(["probe", str(path)], capture_output=True, text=True, check=True)
    fields, pivots, stacks = {}, [], []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "PIVOT":
            pivots.append(parts[1])
        elif parts[0] == "STACK":
            stacks.append({"name": parts[1], "begin": float(parts[2]), "end": float(parts[3])})
        elif len(parts) == 2:
            fields[parts[0]] = parts[1]
    return {"fields": fields, "pivot_nodes": pivots, "stacks": stacks}


@app.post("/predict")
def predict(req: Request):
    if req.pivots not in PIVOT_MODES:
        return {"error": f"pivots must be one of {sorted(PIVOT_MODES)}"}

    with tempfile.TemporaryDirectory() as tmp:
        src = fetch(req.fbx, pathlib.Path(tmp) / "in.fbx")
        found = probe(src)
        f = found["fields"]

        conventions = {
            "unit_meters": float(f.get("unit_meters", 0)),
            "fps": float(f.get("fps", 0)),
            "up_axis": int(f.get("up_axis", -1)),
            "front_axis": int(f.get("front_axis", -1)),
            "nodes": int(f.get("nodes", 0)),
            "meshes": int(f.get("meshes", 0)),
            "bones": int(f.get("bones", 0)),
        }
        pivots_found = int(f.get("geometric_pivots", 0))

        # A request for `preserve` against a file that has pivots is the one combination that
        # must not quietly succeed by baking them. Say so rather than returning a stage that
        # lost them.
        if req.pivots == "preserve" and pivots_found:
            return {
                "error": f"{pivots_found} geometric pivots and pivots=preserve. "
                         "Use helper_nodes to keep them as nodes, or modify_geometry to bake "
                         "them deliberately.",
                "conventions": conventions,
                "pivots_found": pivots_found,
            }

        result = {
            "conventions": conventions,
            "pivots_found": pivots_found,
            "pivot_nodes": found["pivot_nodes"],
            "clips": found["stacks"],
            "requested": {
                "pivots": req.pivots,
                "ufbx_mode": PIVOT_MODES[req.pivots],
                "up_axis": req.up_axis,
                "meters_per_unit": req.meters_per_unit,
                "resample_fps": req.resample_fps or conventions["fps"],
            },
            "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        }

        if req.stub:
            result["stage"] = None
            result["stub"] = True
            return result

        # The USD write is not wired. Godot's fbx_document.cpp sets clean_skin_weights and
        # inherit_mode_handling, and both change what lands, so the skinning path is confirmed
        # against that file before it is trusted rather than after.
        raise NotImplementedError(
            "USD write not wired. The probe half is verified; see README's Status."
        )


@app.get("/health")
def health():
    return {"ok": True}
