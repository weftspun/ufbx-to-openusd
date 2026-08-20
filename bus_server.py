# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The interactor on the iceoryx2 bus.

A transport terminates a client protocol and hands the result to an interactor over the ring.
This is the interactor half for FBX conversion. `server.py`'s HTTP surface stays for standalone
testing and is not how this is reached in the fabric.

THE REPLY CARRIES A HANDLE, NOT THE STAGE. `weft/limits.hpp` caps a value at 128 KiB:

    KEY_BYTES        2 KiB
    VALUE_BYTES    128 KiB
    IN_FLIGHT       32
    ACTION_MS    60000

A converted USD stage is megabytes, and Rin alone is 7.5 MB. So the reply is the path and the
hash of what was written, and the bytes travel on the filesystem the two processes already
share. Chunking is the other option `weft/command.hpp` names for a caller that needs more, and
it is the wrong one here: a stage is opened by path by every consumer downstream, so
reassembling it from chunks only to write it back out buys nothing.

The 60-second action budget is the real constraint on this interactor rather than the size cap.
Converting 22 clips took minutes, so a request converts ONE file and a caller that wants a set
sends a set of requests. A single request that outruns ACTION_MS is a request whose reply
nobody is still waiting for.
"""

import ctypes
import hashlib
import json
import os
import pathlib
import sys

import iceoryx2 as ix

# The limits are the harness's, restated here rather than guessed. They are checked against
# thirdparty/harness/include/weft/limits.hpp, and a drift between the two is a bug in this
# file and not a reason to raise the number locally.
KEY_BYTES = 2 * 1024
VALUE_BYTES = 128 * 1024
ACTION_MS = 60_000

SERVICE = "weft/interactor/ufbx-to-openusd"

# Where converted stages land. Both halves of a request-response pair see it, because
# iceoryx2 is shared memory between processes on one machine and the filesystem is the
# other thing they already share.
OUT_DIR = pathlib.Path(os.environ.get("WEFT_STAGE_DIR", "/var/lib/weft/stages"))


def reply_for(request: dict) -> dict:
    """Convert, and answer with a handle small enough for the envelope."""
    from server import PIVOT_MODES, fetch, probe  # the HTTP path's own machinery, once

    if request.get("pivots", "helper_nodes") not in PIVOT_MODES:
        return {"error": f"pivots must be one of {sorted(PIVOT_MODES)}"}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    src = fetch(request["fbx"], OUT_DIR / "in.fbx")
    found = probe(src)
    f = found["fields"]
    pivots_found = int(f.get("geometric_pivots", 0))

    if request.get("pivots") == "preserve" and pivots_found:
        return {"error": f"{pivots_found} geometric pivots and pivots=preserve"}

    reply = {
        "conventions": {
            "unit_meters": float(f.get("unit_meters", 0)),
            "fps": float(f.get("fps", 0)),
            "up_axis": int(f.get("up_axis", -1)),
            "meshes": int(f.get("meshes", 0)),
            "bones": int(f.get("bones", 0)),
        },
        "pivots_found": pivots_found,
        "clips": found["stacks"],
        "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        # The handle. Absent until the write path is wired, and named rather than omitted so
        # a caller can tell "not written" from "written somewhere I did not say".
        "stage": None,
        "stage_written": False,
    }
    return reply


def encode(payload: dict) -> bytes:
    """JSON, and refuse to send something the envelope cannot hold.

    Truncating to fit would produce a reply that parses and lies. The cap is a property of
    the bus, so exceeding it is this interactor's bug to fix by sending less, not the
    caller's to discover.
    """
    raw = json.dumps(payload, separators=(",", ":")).encode()
    if len(raw) > VALUE_BYTES:
        raw = json.dumps(
            {"error": f"reply is {len(raw)} bytes against a {VALUE_BYTES} cap. "
                      "The handle, not the stage, belongs in the envelope."},
            separators=(",", ":"),
        ).encode()
    return raw


def serve():
    # ctypes.c_uint8, not a binding-specific scalar. The binding takes a ctype, which the
    # signature says and an earlier version of this file guessed wrong twice: neither
    # `ix.u8` nor `ix.NodeEvent` exists. Introspected against 0.9.3 rather than assumed.
    node = ix.NodeBuilder.new().create(ix.ServiceType.Ipc)
    service = (
        node.service_builder(ix.ServiceName.new(SERVICE))
        .request_response(ix.Slice[ctypes.c_uint8], ix.Slice[ctypes.c_uint8])
        .open_or_create()
    )
    server = service.server_builder().create()
    print(f"weft: {SERVICE} listening, cap {VALUE_BYTES} bytes", flush=True)

    # node.wait returns None on a normal tick and raises on shutdown, so the loop condition
    # is the exception rather than a returned enum.
    while True:
        try:
            node.wait(ix.Duration.from_millis(100))
        except Exception:
            break
        while (active := server.receive()) is not None:
            try:
                payload = json.loads(bytes(active.payload()).decode())
                out = reply_for(payload)
            except Exception as exc:  # a dead interactor takes no transport down with it
                out = {"error": f"{type(exc).__name__}: {exc}"}
            raw = encode(out)
            response = active.loan_slice_uninit(len(raw))
            response = response.write_from_slice(raw)
            response.send()


if __name__ == "__main__":
    sys.exit(serve())
