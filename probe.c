// Does this FBX carry non-identity geometric pivots, and does anything else convention-shaped
// differ from what a converter would assume?
//
// The question came up because "preserve geometric pivots if they exist" is conditional, and
// the condition cannot be answered by grepping the binary: FBX declares GeometricTranslation,
// GeometricRotation and GeometricScaling in its property template whether or not any node
// uses them, so a text match finds them in every file ever written.
//
// ufbx exposes the resolved value as ufbx_node.geometry_transform, so this reports the nodes
// where it is not identity, and nothing where it is.
//
// Build:  cc -O2 -o pivot_probe pivot_probe.c ufbx.c -lm
// Run:    ./pivot_probe file.fbx

#include <stdio.h>
#include <math.h>
#include "ufbx.h"

static int near_zero(double v) { return fabs(v) < 1e-9; }
static int near_one(double v) { return fabs(v - 1.0) < 1e-9; }

static int is_identity(ufbx_transform t)
{
	return near_zero(t.translation.x) && near_zero(t.translation.y) && near_zero(t.translation.z)
	    && near_zero(t.rotation.x) && near_zero(t.rotation.y) && near_zero(t.rotation.z)
	    && near_one(t.rotation.w)
	    && near_one(t.scale.x) && near_one(t.scale.y) && near_one(t.scale.z);
}

int main(int argc, char **argv)
{
	if (argc < 2) { fprintf(stderr, "usage: pivot_probe file.fbx\n"); return 2; }

	ufbx_load_opts opts = { 0 };
	// Read the file as authored. Every normalisation ufbx offers would hide the thing being
	// measured: space conversion rewrites transforms, and the pivot-handling options bake
	// geometry transforms away, which is the exact question here.
	ufbx_error err;
	ufbx_scene *scene = ufbx_load_file(argv[1], &opts, &err);
	if (!scene) {
		fprintf(stderr, "ufbx: %s\n", err.description.data);
		return 1;
	}

	size_t with_pivot = 0, meshes = 0, bones = 0;
	printf("nodes\t%zu\n", scene->nodes.count);
	for (size_t i = 0; i < scene->nodes.count; i++) {
		ufbx_node *n = scene->nodes.data[i];
		if (n->mesh) meshes++;
		if (n->bone) bones++;
		if (!is_identity(n->geometry_transform)) {
			with_pivot++;
			if (with_pivot <= 8) {
				ufbx_transform g = n->geometry_transform;
				printf("PIVOT\t%s\tT(%.4f %.4f %.4f)\tR(%.4f %.4f %.4f %.4f)\tS(%.4f %.4f %.4f)\n",
					n->name.data,
					g.translation.x, g.translation.y, g.translation.z,
					g.rotation.x, g.rotation.y, g.rotation.z, g.rotation.w,
					g.scale.x, g.scale.y, g.scale.z);
			}
		}
	}

	// Conventions are data. Report them rather than letting a converter assume.
	printf("meshes\t%zu\nbones\t%zu\n", meshes, bones);
	printf("geometric_pivots\t%zu\n", with_pivot);
	printf("up_axis\t%d\nfront_axis\t%d\nunit_meters\t%.6f\n",
		(int)scene->settings.axes.up, (int)scene->settings.axes.front,
		scene->settings.unit_meters);
	printf("fps\t%.6f\n", scene->settings.frames_per_second);
	printf("anim_stacks\t%zu\n", scene->anim_stacks.count);
	for (size_t i = 0; i < scene->anim_stacks.count && i < 4; i++) {
		ufbx_anim_stack *s = scene->anim_stacks.data[i];
		printf("STACK\t%s\t%.4f\t%.4f\n", s->name.data, s->time_begin, s->time_end);
	}

	ufbx_free_scene(scene);
	return 0;
}
