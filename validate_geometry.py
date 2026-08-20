# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Derive up, forward, handedness and scale from the geometry, then compare with the header.

WHY. `probe.c` reports what the file says about itself. A header is a claim, and a wrong one
produces a body lying on its side or a hundred times too large without erroring. Geometry is
not a claim: a human is about 1.7 m tall, the head is above the feet, and the toes point the
way the body walks. Those hold whatever the metadata says.

Run this against the header and act on the disagreement, not on either alone.

Usage:
    python validate_geometry.py <stage.usd> [--expect-up z] [--expect-meters 0.01]
"""

import argparse
import sys

import numpy as np
from pxr import Usd, UsdGeom, UsdSkel

# A standing adult, used only to pick a decade. It separates metres from centimetres by a
# factor of a hundred, so a loose bound is enough and a tight one would be a false precision.
HUMAN_M = 1.7
AXES = ("x", "y", "z")


def joint_centroids(stage):
    """Rest-pose position per joint, in the same space as the mesh.

    `bindTransforms` is world space *relative to the skeleton prim*, not to the stage. If
    that prim carries a transform, the joints are not where the attribute says either. The
    first version of this file skipped that step and read joints at 152.4 while the mesh
    stood 1.773 tall: centimetres against metres, which is the exact confusion this whole
    validator exists to catch.
    """
    xf_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    for prim in stage.Traverse():
        if prim.IsA(UsdSkel.Skeleton):
            sk = UsdSkel.Skeleton(prim)
            names = [p.split("/")[-1] for p in sk.GetJointsAttr().Get()]
            xf = sk.GetBindTransformsAttr().Get()
            if not xf:
                return {}
            m = np.array(xf_cache.GetLocalToWorldTransform(prim)).reshape(4, 4)
            out = {}
            for n, b in zip(names, xf):
                t = np.array(b.ExtractTranslation())
                out[n] = t @ m[:3, :3] + m[3, :3]
            return out
    return {}


def mesh_points(stage):
    """World-space points.

    The raw `points` attribute is in the prim's own space, and a mesh under a transformed
    parent is not where its points say it is. The first version of this file read them raw
    and concluded the model was Z-up while the skeleton's world-space bind transforms said
    Y-up. Both were read correctly and compared in different spaces.
    """
    xf_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    pts = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            p = UsdGeom.Mesh(prim).GetPointsAttr().Get()
            if not p:
                continue
            m = np.array(xf_cache.GetLocalToWorldTransform(prim)).reshape(4, 4)
            a = np.array(p)
            world = a @ m[:3, :3] + m[3, :3]
            pts.append(world)
    return np.concatenate(pts) if pts else np.empty((0, 3))


def pick(joints, *fragments):
    """First joint whose name contains every fragment, case-insensitively."""
    for name in joints:
        low = name.lower()
        if all(f.lower() in low for f in fragments):
            return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--expect-up", default=None)
    ap.add_argument("--expect-meters", type=float, default=None)
    args = ap.parse_args()

    stage = Usd.Stage.Open(args.stage)
    pts = mesh_points(stage)
    joints = joint_centroids(stage)
    findings, disagreements = {}, []

    # --- THE CROSS-CHECK ---------------------------------------------------------------
    #
    # Mesh and skeleton are two independent measurements of one body. Every space bug this
    # file has had made them disagree, and every one of them still produced a confident
    # single answer because only one source was consulted for each property.
    #
    # So compare the two before trusting either. A body whose mesh stands 1.773 tall and
    # whose joints span 152.4 is not a body with an unusual proportion, it is two readings
    # in different spaces, and no amount of care inside one reader would catch it.
    if pts.size and joints:
        jarr = np.array(list(joints.values()))
        mesh_span = float(np.max(pts.max(axis=0) - pts.min(axis=0)))
        joint_span = float(np.max(jarr.max(axis=0) - jarr.min(axis=0)))
        ratio = mesh_span / joint_span if joint_span else float("inf")
        findings["mesh_span"] = round(mesh_span, 4)
        findings["joint_span"] = round(joint_span, 4)
        findings["mesh_over_joint_span"] = round(ratio, 4)
        # Joints sit inside the body, so the mesh is always a little larger and never
        # smaller by much. Anything outside this band is a space mismatch, not anatomy.
        if not 0.8 < ratio < 1.6:
            disagreements.append(
                f"mesh spans {mesh_span:.3f} and joints span {joint_span:.3f}, a factor of "
                f"{ratio:.1f}. They are being read in different spaces, so every property "
                "below is derived from a body that does not exist"
            )
        # And the two must call the same axis up, independently.
        mesh_up = AXES[int(np.argmax(pts.max(axis=0) - pts.min(axis=0)))]
        joint_up = AXES[int(np.argmax(jarr.max(axis=0) - jarr.min(axis=0)))]
        findings["up_from_mesh"] = mesh_up
        findings["up_from_joints"] = joint_up
        if mesh_up != joint_up:
            disagreements.append(
                f"mesh says up={mesh_up} and joints say up={joint_up}. One of the two is "
                "not in stage space"
            )

    # --- UP, from the longest extent -------------------------------------------------
    #
    # A standing human is much taller than wide or deep, so the longest bounding-box axis is
    # up. This fails on a character posed lying down, which is why the head-above-feet check
    # below has to agree before the answer is used.
    if pts.size:
        extent = pts.max(axis=0) - pts.min(axis=0)
        up_i = int(np.argmax(extent))
        findings["extent"] = [round(float(e), 3) for e in extent]
        findings["up_axis_from_extent"] = AXES[up_i]
        height = float(extent[up_i])
        findings["height_in_file_units"] = round(height, 3)

        # --- SCALE, from that height -------------------------------------------------
        meters_per_unit = HUMAN_M / height if height else 0.0
        findings["meters_per_unit_from_height"] = round(meters_per_unit, 6)
        findings["reads_as"] = (
            "centimetres" if 0.005 < meters_per_unit < 0.02
            else "metres" if 0.5 < meters_per_unit < 2.0
            else "neither, check the model"
        )
    else:
        up_i = None
        findings["extent"] = None

    # --- UP SIGN, from head above feet -----------------------------------------------
    head = pick(joints, "head")
    foot = pick(joints, "foot") or pick(joints, "ankle") or pick(joints, "toe")
    if head and foot and up_i is not None:
        rise = float(joints[head][up_i] - joints[foot][up_i])
        findings["head_minus_foot_on_up"] = round(rise, 3)
        findings["up_sign"] = "+" if rise > 0 else "-"
        if rise <= 0:
            disagreements.append(
                f"head is not above foot on {AXES[up_i]}: the longest axis is not up, "
                "or the rest pose is not standing"
            )

    # --- FORWARD, from the foot pointing at the toe ----------------------------------
    #
    # Toes lead. The ankle-to-toe vector is the walk direction, and it is the only body
    # feature that says forward without needing a face.
    ankle = pick(joints, "foot")
    toe = pick(joints, "toe")
    if ankle and toe and up_i is not None:
        v = joints[toe] - joints[ankle]
        v[up_i] = 0.0  # forward is horizontal; the toe also drops
        n = np.linalg.norm(v)
        if n > 1e-9:
            v = v / n
            f_i = int(np.argmax(np.abs(v)))
            findings["forward_axis"] = ("+" if v[f_i] > 0 else "-") + AXES[f_i]
            findings["forward_vector"] = [round(float(x), 3) for x in v]

    # --- HANDEDNESS, from where the left hand actually is ----------------------------
    #
    # up cross forward gives one side. Which side the joint NAMED left sits on says whether
    # the file's naming matches its geometry. They disagree in mirrored exports, and the
    # disagreement is invisible until a limb is on the wrong side of a fitted body.
    lw, rw = pick(joints, "l_", "wrist"), pick(joints, "r_", "wrist")
    if lw and rw and up_i is not None and "forward_vector" in findings:
        up_v = np.zeros(3)
        up_v[up_i] = 1.0 if findings.get("up_sign", "+") == "+" else -1.0
        fwd = np.array(findings["forward_vector"])
        left_of = np.cross(up_v, fwd)          # right-handed: up x forward points left
        side = float(np.dot(joints[lw] - joints[rw], left_of))
        findings["left_wrist_on_up_cross_forward"] = round(side, 3)
        findings["handedness"] = "right-handed" if side > 0 else "left-handed"
        if side <= 0:
            disagreements.append(
                "the joint named left sits on the right of up x forward: the export is "
                "mirrored, or the names do not follow the geometry"
            )

    # --- compare with what was claimed ------------------------------------------------
    if args.expect_up and findings.get("up_axis_from_extent"):
        if args.expect_up.lower() != findings["up_axis_from_extent"]:
            disagreements.append(
                f"header says up={args.expect_up}, geometry says "
                f"{findings['up_axis_from_extent']}"
            )
    if args.expect_meters and findings.get("meters_per_unit_from_height"):
        got = findings["meters_per_unit_from_height"]
        if not 0.5 < (got / args.expect_meters) < 2.0:
            disagreements.append(
                f"header says {args.expect_meters} m/unit, geometry says about {got}"
            )

    for k, v in findings.items():
        print(f"  {k:34s} {v}")
    print()
    if disagreements:
        for d in disagreements:
            print(f"  DISAGREES  {d}")
        return 1
    print("  geometry agrees with the header on every axis checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
