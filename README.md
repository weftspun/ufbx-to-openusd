# ufbx-to-openusd

FBX in, OpenUSD out, through [ufbx](https://github.com/ufbx/ufbx). An interactor per RFD 0036,
and the entry point to RFD 0053's internal format for anything that arrives as FBX.

## Why ufbx and not the other reader

FBX is a closed binary format, so the question is which reader to trust rather than whether to
write one. Godot reads FBX through ufbx, and this follows Godot's option choices rather than
inventing a second opinion. `modules/fbx/fbx_document.cpp` is the reference.

Blender ships ufbx too, as `wm.fbx_import`. It is usable and it is **not** interchangeable with
`bpy.ops.import_scene.fbx`, which is the legacy Python importer. The two differ on units, axis
handling, leaf bones and skin clusters. This repository names the distinction because getting
it wrong produces a plausible result rather than an error.

## Conventions are parsed, never assumed

The reason this exists as a tool rather than a one-line call. Every FBX states its own up axis,
front axis, unit scale and frame rate, and a converter that assumes any of them yields
something that looks right and is wrong.

Measured on the o3de motion-matching set, which motivated this:

| convention | value | what assuming would cost |
| --- | --- | --- |
| `unit_meters` | 0.01 | the file is in centimetres, so a body comes out 100x too large |
| `fps` | 30 | Blender's scene default is 25, and resampling to it silently rewrites every key |
| up axis | +Z | a Y-up assumption lays the character on its side |

`probe.c` reports all of them and exits without converting. Run it first on anything unfamiliar.

### And then check them against the geometry

A header is a claim. `validate_geometry.py` derives the same four properties from the model
itself and reports where the two disagree.

| property | derived from |
| --- | --- |
| up | the longest bounding-box extent, confirmed by the head sitting above the foot |
| scale | that height against a 1.7 m adult, which separates metres from centimetres |
| forward | the ankle-to-toe vector, flattened onto the ground plane |
| handedness | which side of `up x forward` the joint named left actually sits on |

Handedness is the one worth having. A mirrored export keeps every name and moves the geometry,
so nothing reads as wrong until a limb lands on the wrong side of a fitted body.

Measured on Rin, converted through this path:

    up          z          extent [1.13, 0.408, 1.773]
    scale       metres     1.773 tall, head 1.427 above foot
    forward     -y
    handedness  right      left wrist +0.984 on up x forward

Note the scale. The FBX is centimetres and the converted stage is metres, because the import
normalises units the way Godot does with `target_unit_meters = 1.0`. Reading the header alone
would call the stage wrong.

**Both readings have to be in the same space or the comparison is meaningless.** Mesh points
live in their prim's space and `bindTransforms` live in the skeleton prim's, so each needs its
local-to-world applied first. Skipping that produced a confident Z-up answer from a mesh in
one space and joints in another, and the joints read 152.4 while the mesh stood 1.773 tall.

### The cross-check is what makes that catchable

Mesh and skeleton are two independent measurements of one body, so they are compared with each
other before either is trusted. A body whose mesh spans 1.773 and whose joints span 152.4 is
not unusual anatomy, it is two spaces, and no amount of care inside a single reader would find
it. Joints sit inside the body, so the ratio is a little above one and never far from it.

    mesh_span 1.773   joint_span 1.612   ratio 1.0998   up from both: z

`test_validate_geometry.py` holds one control per failure this validator has actually had,
plus one per property it claims to measure. Each builds a stage in memory, breaks exactly one
thing, and asserts the validator says so.

    ok   positive control: a clean body validates
    ok   mesh scaled 100x against unscaled joints
    ok   skeleton rotated 90 degrees against unrotated mesh
    ok   whole body mirrored on x
    ok   body upside down
    ok   body a hundred times too large, header says metres

The first two are the bugs above, kept as tests so they cannot come back quietly.

## Geometric pivots

ufbx resolves geometric transforms and offers three handlings. The choice is the caller's,
because the right one depends on what the output is for.

| mode | effect |
| --- | --- |
| `helper_nodes` | pivots survive as explicit nodes. Nothing is lost, the hierarchy grows |
| `modify_geometry` | pivots are baked into vertices. Visually identical, the pivot stops existing |
| `preserve` | fail rather than bake, when a pivot is found and cannot be kept |

Godot exposes the same choice as `allow_geometry_helper_nodes`. Blender's importer exposes no
option at all and bakes, which is why "preserve geometric pivots" cannot be honoured through
Blender and needs this path.

**The o3de set has none.** `geometric_pivots 0` across all 23 files, character included. The
FBX property template declares `GeometricTranslation`, `GeometricRotation` and
`GeometricScaling` in every file ever written, so grepping the binary finds them and proves
nothing. Only a resolved read answers it.

## The bus is the interface

A transport terminates a client protocol and hands the result to an interactor over the
iceoryx2 ring. `bus_server.py` is that half. The HTTP surface below stays for standalone
testing and is not how this is reached in the fabric.

    weft/interactor/ufbx-to-openusd

**The reply carries a handle, not the stage.** `weft/limits.hpp` caps a value at 128 KiB and a
key at 2 KiB, with 32 in flight and a 60-second action budget. A converted stage is megabytes
and Rin alone is 7.5 MB, so the reply is the path and the hash, and the bytes travel on the
filesystem the two processes already share. `encode` refuses to send an oversized reply rather
than truncating one, because a truncated reply parses and lies.

Chunking is the other option `weft/command.hpp` names, and it is wrong here: every consumer
downstream opens a stage by path, so reassembling one from chunks to write it back out buys
nothing.

**The 60-second budget binds harder than the size cap.** Converting 22 clips took minutes, so a
request converts one file and a caller wanting a set sends a set of requests.

Python and C++ both reach the bus. C++ links `weft::harness`, which dlopens
`libiceoryx2_ffi_c` through a generated dispatch table so no plane links iceoryx2 itself.
Python uses the `iceoryx2` binding, 0.9.3, and takes `ctypes.c_uint8` slices.

**iceoryx2 needs POSIX shared memory.** Node creation fails on Windows with `InternalError`,
so this runs under Linux or WSL.

## HTTP, for standalone testing

`POST /predict`:

| input | type | default | note |
| --- | --- | --- | --- |
| `fbx` | path or URL or base64 | required | |
| `pivots` | str | `helper_nodes` | `helper_nodes`, `modify_geometry`, or `preserve` |
| `up_axis` | str | `z` | the USD stage's up axis. The FBX's own is reported either way |
| `meters_per_unit` | float | `1.0` | output scale. The FBX's own is reported either way |
| `resample_fps` | float | `0` | 0 keeps the file's rate rather than choosing one |

Returns `{stage, conventions, pivots_found, clips, sha256}`. `conventions` carries what the
file said, not what was requested, so a caller can tell a conversion from an assumption.

## Build

```sh
docker build --target contract -t ufbx-to-openusd:contract .
docker run --rm -p 8000:8000 ufbx-to-openusd:contract
curl -X POST localhost:8000/predict -d @test_input.json -H 'Content-Type: application/json'
```

`ufbx.c` and `ufbx.h` are fetched at build time rather than vendored, so the version is a
build argument and not a copy nobody updates.

## Status

**Probe verified, conversion not yet wired.** `probe.c` compiles and runs: it read 23 o3de
files and reported 0 geometric pivots, 0.01 unit_meters, 30 fps and +Z up on every one, which
is what corrected two wrong values in an earlier USD stage built through Blender.

`server.py` dispatches and returns the probe's findings. The USD write is
`NotImplementedError` outside stub mode. Confirm against Godot's `fbx_document.cpp` before
trusting the skinning path, since `clean_skin_weights` and `inherit_mode_handling` both change
what lands.

## Licence

Licensed under either of Apache-2.0 ([LICENSE-APACHE](LICENSE-APACHE)) or MIT
([LICENSE-MIT](LICENSE-MIT)) at your option. `SPDX-License-Identifier: Apache-2.0 OR MIT`

ufbx is MIT and is fetched, not vendored. FBX is a trademark of Autodesk, used here only to
name the format this reads.
