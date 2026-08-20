# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Negative controls for the geometric validator.

WHY THESE EXIST. A validator that passes on a good file has shown only that it is not
uniformly hostile. `validate_geometry.py` shipped two bugs before it caught anything about the
data, and both returned confident, plausible answers:

  * mesh points read without their prim's transform, so the mesh sat in one space and the
    skeleton in another. It reported Z-up from a mesh whose joints said Y-up.
  * bind transforms read without the skeleton prim's transform, so joints read 152.4 against a
    mesh 1.773 tall. Centimetres against metres, inside one comparison.

Neither raised anything. Each is now a test that fails if the bug returns, and each was written
from the failure rather than imagined in advance, which is the only reason the band and the
axis check are set where they are.

Every control builds a stage in memory, breaks exactly one property, and asserts the validator
says so. A control that passes is a control that has stopped testing anything.

Run:  python test_validate_geometry.py
"""

import pathlib
import subprocess
import sys
import tempfile

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel

HERE = pathlib.Path(__file__).parent
VALIDATOR = HERE / "validate_geometry.py"

# A minimal standing body: a tall box for the mesh, and joints down its middle with a foot,
# a head, a toe ahead of the ankle, and wrists to either side. Enough to exercise every
# property and small enough to read.
JOINTS = {
    "root":        (0.00, 0.00, 0.00),
    "C_head_JNT":  (0.00, 0.00, 1.60),
    "R_foot_JNT":  (-0.10, 0.00, 0.08),
    "L_foot_JNT":  (0.10, 0.00, 0.08),
    "R_toe_JNT":   (-0.10, -0.15, 0.03),
    "L_toe_JNT":   (0.10, -0.15, 0.03),
    "R_wrist_JNT": (-0.65, 0.00, 0.90),
    "L_wrist_JNT": (0.65, 0.00, 0.90),
}
BOX = np.array([
    [-0.30, -0.15, 0.0], [0.30, -0.15, 0.0], [0.30, 0.15, 0.0], [-0.30, 0.15, 0.0],
    [-0.30, -0.15, 1.7], [0.30, -0.15, 1.7], [0.30, 0.15, 1.7], [-0.30, 0.15, 1.7],
])


def build(path, mesh_xform=None, skel_xform=None, points=None, joints=None):
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    root = UsdGeom.Xform.Define(stage, "/Body")
    stage.SetDefaultPrim(root.GetPrim())

    mesh = UsdGeom.Mesh.Define(stage, "/Body/Mesh")
    pts = BOX if points is None else points
    mesh.CreatePointsAttr([Gf.Vec3f(*p) for p in pts])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    if mesh_xform is not None:
        UsdGeom.Xformable(mesh).AddTransformOp().Set(Gf.Matrix4d(*mesh_xform.flatten()))

    skel = UsdSkel.Skeleton.Define(stage, "/Body/Skel")
    src = JOINTS if joints is None else joints
    skel.CreateJointsAttr([f"/{n}" for n in src])
    skel.CreateBindTransformsAttr([
        Gf.Matrix4d().SetTranslate(Gf.Vec3d(*p)) for p in src.values()
    ])
    skel.CreateRestTransformsAttr([
        Gf.Matrix4d().SetTranslate(Gf.Vec3d(*p)) for p in src.values()
    ])
    if skel_xform is not None:
        UsdGeom.Xformable(skel).AddTransformOp().Set(Gf.Matrix4d(*skel_xform.flatten()))

    stage.GetRootLayer().Save()
    return path


def run(path, *args):
    out = subprocess.run(
        [sys.executable, str(VALIDATOR), str(path), *args], capture_output=True, text=True
    )
    return out.returncode, out.stdout + out.stderr


def scale4(s):
    m = np.eye(4)
    m[0, 0] = m[1, 1] = m[2, 2] = s
    return m


def rot_x90():
    m = np.eye(4)
    m[1, 1], m[1, 2], m[2, 1], m[2, 2] = 0, 1, -1, 0
    return m


def mirror_x():
    m = np.eye(4)
    m[0, 0] = -1
    return m


def look_at(eye, target, up, forward_sign=-1.0):
    """A view matrix, with the forward axis as an argument.

    `forward_sign=-1` is OpenGL, looking down -Z. `+1` is OpenCV, looking down +Z. The two
    differ by one negated row and neither errors when handed to the wrong projector.
    """
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    M = np.eye(4)
    M[0, :3], M[1, :3], M[2, :3] = s, u, forward_sign * f
    M[:3, 3] = -M[:3, :3] @ eye
    return M


CONTROLS = []


def control(name, must_match):
    def deco(fn):
        CONTROLS.append((name, fn, must_match))
        return fn
    return deco


# --- the two bugs this file was written from -------------------------------------------

@control("mesh scaled 100x against unscaled joints", "different spaces")
def _mesh_only_scaled(p):
    # The centimetres-against-metres bug: one reader normalised, the other did not.
    return build(p, mesh_xform=scale4(100.0))


@control("skeleton rotated 90 degrees against unrotated mesh", "up=")
def _skel_only_rotated(p):
    # The Z-up-mesh against Y-up-joints bug, made explicit.
    return build(p, skel_xform=rot_x90())


# --- the properties the validator claims to measure -------------------------------------

@control("whole body mirrored on x", "mirrored")
def _mirrored(p):
    j = {n: (-x, y, z) for n, (x, y, z) in JOINTS.items()}
    return build(p, points=BOX * np.array([-1, 1, 1]), joints=j)


@control("body upside down", "head is not above foot")
def _upside_down(p):
    j = {n: (x, y, -z) for n, (x, y, z) in JOINTS.items()}
    return build(p, points=BOX * np.array([1, 1, -1]), joints=j)


@control("body a hundred times too large, header says metres", "m/unit")
def _too_large(p):
    j = {n: (x * 100, y * 100, z * 100) for n, (x, y, z) in JOINTS.items()}
    return build(p, points=BOX * 100.0, joints=j)


def camera_controls(tmp):
    """The camera-convention failure, kept as a control.

    A projector expecting +Z-forward, handed a -Z-forward matrix, clamps every vertex to its
    minimum depth and returns a uniform map with full coverage. Nothing raises.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("vg", VALIDATOR)
    vg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vg)

    body = BOX.copy()
    eye = np.array([0.0, -4.0, 0.85])
    target = body.mean(axis=0)
    up = np.array([0.0, 0.0, 1.0])

    rows = []
    # Right way round for a +Z-forward projector: nothing behind the pinhole.
    _, ok_problems = vg.check_camera_convention(body, look_at(eye, target, up, +1.0))
    rows.append(("camera +Z forward, the convention the projector expects",
                 len(ok_problems) == 0, ok_problems))
    # Inverted: every vertex behind. This is the bug.
    _, bad_problems = vg.check_camera_convention(body, look_at(eye, target, up, -1.0))
    rows.append(("camera -Z forward against a +Z projector",
                 len(bad_problems) > 0, bad_problems))
    # A clamped depth map: every vertex at one depth reads as valid and is not.
    flat = np.tile(np.array([[0.0, 0.0, 1e-4]]), (len(body), 1))
    _, flat_problems = vg.check_camera_convention(flat, np.eye(4), name="clamped")
    rows.append(("every vertex at one depth, which is a clamp firing",
                 len(flat_problems) > 0, flat_problems))
    return rows


def main():
    with tempfile.TemporaryDirectory() as tmp:
        good = build(pathlib.Path(tmp) / "good.usda")
        rc, out = run(good, "--expect-up", "z", "--expect-meters", "1.0")
        print(f"  {'ok  ' if rc == 0 else 'FAIL'} positive control: a clean body validates")
        if rc != 0:
            print(out)
            return 1

        failures = []
        for i, (name, fn, must_match) in enumerate(CONTROLS):
            path = fn(pathlib.Path(tmp) / f"c{i}.usda")
            rc, out = run(path, "--expect-up", "z", "--expect-meters", "1.0")
            caught = rc != 0 and must_match.lower() in out.lower()
            print(f"  {'ok  ' if caught else 'FAIL'} {name}")
            if not caught:
                failures.append(name)
                print(f"       expected a disagreement mentioning {must_match!r}")
                print("       " + " / ".join(l.strip() for l in out.splitlines() if l.strip())[:200])

        for name, passed, detail in camera_controls(tmp):
            print(f"  {'ok  ' if passed else 'FAIL'} {name}")
            if not passed:
                failures.append(name)
                print(f"       {detail}")

        print()
        if failures:
            print(f"{len(failures)} control(s) did not fail. The validator is not gating.")
            return 1
        print(f"{len(CONTROLS)} stage controls plus 3 camera controls, "
              "each caught for its own reason.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
