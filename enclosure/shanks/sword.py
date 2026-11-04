"""Shanks' sabre (Gryphon), in its scabbard, on the snail's left flank: it follows the curve
of Ace's dagger and covers it, from a hilt by the body's little tentacle down to the foot.

    Blender -b --python sword.py -- <shanks.stl> <out_dir>

Writes sword.stl (a separate part: black scabbard and grip, gold guard and fittings).
The snail looks towards -y; its left flank is +x.
"""
import math
import sys
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

SNAIL, OUT = sys.argv[-2], sys.argv[-1]
# the scabbard's path on the flank, (y, z), from the guard down to the tip
PATH = [(17, 41), (23, 36), (29, 30), (36, 22.5), (43, 16.5), (50, 12.5), (58, 10), (67, 8.5), (76, 8)]   # over Ace's dagger (y 25-52)
WIDTH, THICK = 9.5, 5.2       # scabbard cross-section: across the flank, and off it
CLEAR = 0.8                   # off whatever stands out under it (the dagger)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=SNAIL)
snail = bpy.context.selected_objects[0]
tree = BVHTree.FromObject(snail, bpy.context.evaluated_depsgraph_get())


def surface_x(y, z, r=4.5):
    """How far the flank (and anything on it) stands out around (y, z)."""
    best = -1e9
    for dy in (-r, 0, r):
        for dz in (-r, 0, r):
            h = tree.ray_cast(Vector((150, y + dy, z + dz)), Vector((-1, 0, 0)))
            if h[0] is not None:
                best = max(best, h[0].x)
    return best


def smooth_path(pts, n=120):
    """Catmull-Rom through the points, n samples."""
    out = []
    p = [pts[0]] + pts + [pts[-1]]
    for i in range(n):
        u = i / (n - 1) * (len(pts) - 1)
        k = min(int(u), len(pts) - 2); t = u - k
        p0, p1, p2, p3 = (Vector(p[k + j]) for j in range(4))
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                          + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t))
    return out


# the centre line, sitting on the flank: x follows what stands out, then smoothed
yz = smooth_path([Vector(p) for p in PATH])
xs = [surface_x(p.x, p.y) + CLEAR + THICK / 2 for p in yz]
for _ in range(30):                                   # a sabre does not follow every bump
    xs = [xs[0]] + [max(xs[i], (xs[i - 1] + xs[i] + xs[i + 1]) / 3) * 0.5 + (xs[i - 1] + xs[i] + xs[i + 1]) / 6
                    for i in range(1, len(xs) - 1)] + [xs[-1]]
line = [Vector((x, p.x, p.y)) for x, p in zip(xs, yz)]

bm = bmesh.new()


def tube(points, section, closed_ends=True):
    """Sweep a 2D section (list of (u, v): u across the flank, v off it) along points."""
    rings = []
    for i, c in enumerate(points):
        across, off = frame(points, i)
        rings.append([bm.verts.new(c + across * u + off * v) for u, v in section(i / (len(points) - 1))])
    stitch(rings, closed_ends)


def frame(points, i):
    """Across the flank and off it at point i, square to the line."""
    t = (points[min(i + 1, len(points) - 1)] - points[max(i - 1, 0)]).normalized()
    off = Vector((1, 0, 0))                                   # off the flank
    across = t.cross(off).normalized()
    return across, across.cross(t).normalized()


def stitch(rings, closed_ends):
    """The faces between one ring and the next, and both ends."""
    for a, b in zip(rings, rings[1:]):
        n = len(a)
        for j in range(n):
            bm.faces.new((a[j], a[(j + 1) % n], b[(j + 1) % n], b[j]))
    if closed_ends:
        bm.faces.new(rings[0][::-1]); bm.faces.new(rings[-1])


def ellipse(w, h, n=20):
    return [(w / 2 * math.cos(2 * math.pi * k / n), h / 2 * math.sin(2 * math.pi * k / n)) for k in range(n)]


# scabbard: tapers to a rounded tip, a gold chape at the end and a throat at the top
tube(line, lambda t: ellipse(WIDTH * (1 - 0.35 * t ** 2), THICK * (1 - 0.2 * t)))
for t0, length in ((0.0, 4), (0.93, 6)):                     # throat and chape bands, standing proud
    i0 = int(t0 * (len(line) - 1))
    seg = line[i0:i0 + length]
    if len(seg) > 1:
        tube(seg, lambda t: ellipse(WIDTH * (1 - 0.35 * t0 ** 2) + 1.2, THICK + 1.0))

# hilt: the guard across the scabbard's mouth, the grip and the pommel going back up the line
top, nxt = line[0], line[1]
axis = (top - nxt).normalized()                              # up and forward, out of the scabbard
off = Vector((1, 0, 0)); across = axis.cross(off).normalized(); off = across.cross(axis).normalized()
guard_c = top + axis * 1.5
# Gryphon's guard: a bar across, its ends curling back towards the blade
guard = []
for k in range(-12, 13):
    s = k / 12
    curl = 3.5 * s ** 4                                      # the ends sweep back
    guard.append(guard_c + across * (11 * s) - axis * curl)
tube(guard, lambda t: ellipse(3.0, 3.0 + 1.2 * (1 - abs(2 * t - 1))), closed_ends=True)
for end in (guard[0], guard[-1]):                            # little balls at the tips
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=2.0,
                              matrix=__import__("mathutils").Matrix.Translation(end))
# grip, wrapped: a tube whose section swells in rings
grip = [guard_c + axis * (2 + 18 * k / 30) for k in range(31)]
tube(grip, lambda t: ellipse(5.4 + 0.5 * math.cos(t * 2 * math.pi * 7), 5.0 + 0.5 * math.cos(t * 2 * math.pi * 7)))
bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=3.6,
                          matrix=__import__("mathutils").Matrix.Translation(grip[-1] + axis * 2.2))

bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
me = bpy.data.meshes.new("sword"); bm.to_mesh(me)
sword = bpy.data.objects.new("sword", me); bpy.context.collection.objects.link(sword)
# one clean solid from the overlapping pieces
bpy.context.view_layer.objects.active = sword
m = sword.modifiers.new("remesh", "REMESH"); m.mode, m.voxel_size = "VOXEL", 0.25
bpy.ops.object.modifier_apply(modifier=m.name)
bpy.ops.object.select_all(action="DESELECT"); sword.select_set(True)
bpy.ops.wm.stl_export(filepath=f"{OUT}/sword.stl", export_selected_objects=True)
print("SWORD", len(sword.data.polygons), "faces; top", tuple(round(c, 1) for c in line[0]), "tip", tuple(round(c, 1) for c in line[-1]))
print("DONE")
