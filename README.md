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

## Interface

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
