"""Shanks' Den Den Mushi, from the Ace one: Ace's hat off, Luffy's straw hat on.

    Blender -b --python shanks.py -- <ace.stl> <out_dir>

Writes body.stl (the snail without its hat), hat.stl (the straw hat, a separate part,
cut where it meets the shell) and preview_*.png. Model units are mm; the snail
faces -y, z up.
"""
import math
import sys
import bmesh
import bpy
from mathutils import Matrix, Vector

ACE, OUT = sys.argv[-2], sys.argv[-1]

HEAD = Vector((17, 2, 0))     # centre of the head (and of Ace's hat), seen from above
CUT_Z = 64                    # Ace's hat goes from here up; the eyes stay below
CUT_R = 34
SHELL_Y = 21                  # keep everything behind this: the shell (its front starts at 22)
HAT_TILT = 14                 # degrees, tipped back like Ace's: its brim covers what is left of his
HAT_BASE = 76                 # height of the crown's base at the centre of the head
VOXEL = 0.3                   # remesh: the model is overlapping shells, this makes it one solid


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def boolean(target, cutter, op):
    m = target.modifiers.new("bool", "BOOLEAN")
    m.operation, m.solver, m.object = op, "EXACT", cutter
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=m.name)


def mesh_object(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    return ob


def revolve(name, profile, steps=128):
    """A closed (r, z) profile spun around z."""
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0, z)) for r, z in profile]
    edges = [bm.edges.new((verts[i], verts[(i + 1) % len(verts)])) for i in range(len(verts))]
    bmesh.ops.spin(bm, geom=verts + edges, cent=(0, 0, 0), axis=(0, 0, 1), angle=2 * math.pi, steps=steps, use_duplicate=False)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm)


def box(name, lo, hi):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    for v in bm.verts:
        v.co = Vector([lo[i] + (v.co[i] + 0.5) * (hi[i] - lo[i]) for i in range(3)])
    return mesh_object(name, bm)


def export(ob, path):
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True, apply_modifiers=True)


reset()
bpy.ops.wm.stl_import(filepath=ACE)
body = bpy.context.selected_objects[0]
body.name = "body"
# one watertight solid: the file is a pile of overlapping shells, booleans need inside and outside
m = body.modifiers.new("remesh", "REMESH")
m.mode, m.voxel_size, m.use_smooth_shade = "VOXEL", VOXEL, True
bpy.context.view_layer.objects.active = body
bpy.ops.object.modifier_apply(modifier=m.name)

# 1. Ace's hat off: a cylinder around the head, above the eyes, in front of the shell
cyl = revolve("cutter", [(0, CUT_Z), (CUT_R, CUT_Z), (CUT_R, 120), (0, 120)], steps=96)
cyl.location = (HEAD.x, HEAD.y, 0)
front = box("front", (-80, -80, 0), (80, SHELL_Y, 130))
boolean(cyl, front, "INTERSECT")
bpy.data.objects.remove(front)
boolean(body, cyl, "DIFFERENCE")
bpy.data.objects.remove(cyl)
# the back of Ace's bead band and brim, over the front of the shell: above 82 mm (the shell
# stays under 81.5 there, the new brim passes at that height)
beads = revolve("beads", [(0, 81), (CUT_R, 81), (CUT_R, 120), (0, 120)], steps=96)
beads.location = (HEAD.x, HEAD.y, 0)
band = box("band", (-80, SHELL_Y - 1, 0), (80, 31, 130))
boolean(beads, band, "INTERSECT")
bpy.data.objects.remove(band)
boolean(body, beads, "DIFFERENCE")
bpy.data.objects.remove(beads)
# just behind the head, under the drooping back of the new brim: lower still
low = revolve("low", [(0, 79), (CUT_R, 79), (CUT_R, 120), (0, 120)], steps=96)
low.location = (HEAD.x, HEAD.y, 0)
band = box("band", (-80, SHELL_Y - 1, 0), (80, 29, 130))
boolean(low, band, "INTERSECT")
bpy.data.objects.remove(band)
boolean(body, low, "DIFFERENCE")
bpy.data.objects.remove(low)

from mathutils.bvhtree import BVHTree
def remove_thin_brim():
    # what is left of Ace's brim behind the head: a thin plate (2-3 mm) on the shell. The shell
    # and the body are solid, so the thin faces there are the brim's: cut a skin around them.
    tree = BVHTree.FromObject(body, bpy.context.evaluated_depsgraph_get())
    bm = bmesh.new()
    thin = brim_prisms(tree, bm)
    print("THIN_FACES", thin)
    if thin:
        cut_skin(bm)


def on_brim(c):
    """Where the brim was: above 66 mm, behind the head, 16 to 38 mm around its centre."""
    return c.z >= 66 and c.y >= HEAD.y + 6 and 16 < math.hypot(c.x - HEAD.x, c.y - HEAD.y) < 38


def brim_prisms(tree, bm):
    """A prism through the plate under each thin face where the brim was, into bm; how many."""
    thin = 0
    for f in body.data.polygons:
        c, n = f.center, f.normal
        if not on_brim(c):
            continue
        hit = tree.ray_cast(c - n * 0.05, -n, 4.0)
        if hit[0] is None:
            continue
        thin += 1
        prism_through(bm, f, hit[3] + 0.1)
    return thin


def prism_through(bm, f, t):
    """A prism on face f through a plate t thick, a little beyond both faces."""
    n = f.normal
    ring = [body.data.vertices[i].co for i in f.vertices]
    lo = [bm.verts.new(v + n * 0.4) for v in ring]
    hi = [bm.verts.new(v - n * (t + 0.4)) for v in ring]
    bm.faces.new(lo); bm.faces.new(hi[::-1])
    for i in range(len(ring)):
        j = (i + 1) % len(ring)
        bm.faces.new((lo[i], hi[i], hi[j], lo[j]))


def cut_skin(bm):
    """The pile of prisms made one clean solid, and cut out of the body."""
    skin = mesh_object("skin", bm)
    m = skin.modifiers.new("remesh", "REMESH")
    m.mode, m.voxel_size = "VOXEL", VOXEL
    bpy.context.view_layer.objects.active = skin
    bpy.ops.object.modifier_apply(modifier=m.name)
    boolean(body, skin, "DIFFERENCE")
    bpy.data.objects.remove(skin)


remove_thin_brim()
remove_thin_brim()          # a second pass takes the ragged edges the first one leaves
# drop the loose debris: keep the snail, the biggest piece
bpy.ops.object.select_all(action="DESELECT")
body.select_set(True); bpy.context.view_layer.objects.active = body
bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.separate(type="LOOSE"); bpy.ops.object.mode_set(mode="OBJECT")
pieces = sorted(bpy.context.selected_objects, key=lambda o: len(o.data.vertices), reverse=True)
print("PIECES", len(pieces), [len(o.data.vertices) for o in pieces[:6]])
body = pieces[0]; body.name = "body"
for o in pieces[1:]:
    bpy.data.objects.remove(o)

# 2. Luffy's straw hat: a domed crown, a red band, a brim that droops a little at its edge
crown_r, brim_r = 15.5, 32
BRIM_R = 31          # brim radius at the front and the sides (Luffy's wide brim)
BRIM_BACK = 25       # at the back, where it would sink into the shell
def brim(f):   # f from 0 (at the crown) to 1 (the edge)
    return 16.2 + f * (BRIM_R - 16.2)
profile = [   # Luffy's: a round crown, the red band, a brim drooping towards its edge
    (0, 19.0), (5, 18.6), (9, 17.3), (12, 15.2), (14, 12.2), (15.2, 8.8), (15.5, 5.2),   # crown, a dome
    (16.1, 4.8), (16.1, 0.9), (15.5, 0.8),                                               # the band, standing proud
    (brim(0), 0.6), (brim(0.33), 0.0), (brim(0.67), -1.2), (brim(0.9), -2.6), (brim(1), -3.6),          # brim, top
    (brim(1) - 0.6, -4.6), (brim(0.67), -2.8), (brim(0.33), -1.5), (brim(0), -1.0), (0, -1.0)           # underside
]
hat = revolve("hat", profile)
# the brim narrows towards the back (+y), where the shell is
bm = bmesh.new()
outline = []
for i in range(144):
    a = 2 * math.pi * i / 144
    toward_back = max(0.0, math.sin(a))          # 1 straight back, 0 at the sides and the front
    r = BRIM_R - (BRIM_R - BRIM_BACK) * toward_back ** 1.5
    outline.append((r * math.cos(a), r * math.sin(a)))
bottom = [bm.verts.new((x, y, -20)) for x, y in outline]
top = [bm.verts.new((x, y, 40)) for x, y in outline]
bm.faces.new(bottom[::-1]); bm.faces.new(top)
for i in range(len(outline)):
    j = (i + 1) % len(outline)
    bm.faces.new((bottom[i], bottom[j], top[j], top[i]))
mask = mesh_object("mask", bm)
boolean(hat, mask, "INTERSECT")
bpy.data.objects.remove(mask)
hat.matrix_world = Matrix.Translation((HEAD.x, HEAD.y + 1, HAT_BASE)) @ Matrix.Rotation(math.radians(HAT_TILT), 4, "X")
bpy.context.view_layer.objects.active = hat
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
# the head goes on up into the crown: no gap between the cut and the hat
bm = bmesh.new()
for v in body.data.vertices:          # the head just under the cut, in front of the shell
    if CUT_Z - 2.5 < v.co.z < CUT_Z + 0.5 and v.co.y < SHELL_Y - 2 and math.hypot(v.co.x - HEAD.x, v.co.y - HEAD.y) < 24:
        bm.verts.new((v.co.x, v.co.y, CUT_Z - 1))
for i in range(64):                    # the crown's base, inside the hat
    a = 2 * math.pi * i / 64
    bm.verts.new((HEAD.x + 14.5 * math.cos(a), HEAD.y + 2.5 + 14.5 * math.sin(a), HAT_BASE + 2))
bmesh.ops.convex_hull(bm, input=bm.verts)
cap = mesh_object("cap", bm)
boolean(body, cap, "UNION")
bpy.data.objects.remove(cap)

shell_part = box("shell_part", (-80, 18, 0), (80, 120, 130))
boolean(shell_part, body, "INTERSECT")
probe = hat.copy(); probe.data = hat.data.copy(); bpy.context.collection.objects.link(probe)
crown = revolve("crown", [(0, 0), (17.5, 0), (17.5, 130), (0, 130)], steps=64)
crown.location = (HEAD.x, HEAD.y + 1, 0)
boolean(probe, crown, "DIFFERENCE")      # the brim alone
bpy.data.objects.remove(crown)
boolean(probe, shell_part, "INTERSECT")
pbm = bmesh.new(); pbm.from_mesh(probe.data)
print("INSIDE_SHELL", BRIM_R, round(pbm.calc_volume(), 1), "mm3")
bpy.data.objects.remove(probe); bpy.data.objects.remove(shell_part)
if __import__("os").environ.get("PROBE_ONLY"):
    raise SystemExit
# 4. Shanks' three scars over his left eye (the snail looks towards -y: its left eye is at +x)
LEFT_EYE = Vector((26, 0, 58))
SCAR_W, SCAR_DEPTH = 1.1, 0.9
tree = BVHTree.FromObject(body, bpy.context.evaluated_depsgraph_get())
bm = bmesh.new()
for dx in (-3.2, 0, 3.2):
    top = Vector((LEFT_EYE.x + dx + 2.2, 0, 67.5))     # slanting a little, like his
    bot = Vector((LEFT_EYE.x + dx - 2.2, 0, 50.5))
    along = (bot - top).normalized()
    side = Vector((along.z, 0, -along.x)) * (SCAR_W / 2)
    strip = []
    n = 34
    for i in range(n + 1):
        c = top.lerp(bot, i / n)
        hit = tree.ray_cast(Vector((c.x, -80, c.z)), Vector((0, 1, 0)))
        if hit[0] is None:
            continue
        # taper the ends so the scar fades in and out
        k = min(1.0, min(i, n - i) / 4 + 0.25)
        d = SCAR_DEPTH * k
        y_back = hit[0].y + d
        y_front = hit[0].y - 3
        w = side * k
        strip.append([bm.verts.new((c.x - w.x, y_back, c.z - w.z)), bm.verts.new((c.x + w.x, y_back, c.z + w.z)),
                      bm.verts.new((c.x + w.x, y_front, c.z + w.z)), bm.verts.new((c.x - w.x, y_front, c.z - w.z))])
    bm.faces.new(strip[0][::-1]); bm.faces.new(strip[-1])
    for a_, b_ in zip(strip, strip[1:]):
        for j in range(4):
            bm.faces.new((a_[j], a_[(j + 1) % 4], b_[(j + 1) % 4], b_[j]))
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
scars = mesh_object("scars", bm)
boolean(body, scars, "DIFFERENCE")
bpy.data.objects.remove(scars)

export(body, f"{OUT}/body.stl")
export(hat, f"{OUT}/hat.stl")
merged = body.copy(); merged.data = body.data.copy(); bpy.context.collection.objects.link(merged)
boolean(merged, hat, "UNION")
export(merged, f"{OUT}/shanks.stl")
bpy.data.objects.remove(merged)
for o in (body, hat):
    print("VOLUME", o.name, round(sum(f.area for f in o.data.polygons), 1), "mm2 surface,", len(o.data.polygons), "faces")

# 3. previews (Workbench, flat colours)
def material(ob, rgb):
    ob.color = (*rgb, 1)

material(body, (0.93, 0.80, 0.62))
material(hat, (0.95, 0.80, 0.35))
scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.color_type = "OBJECT"
scene.display.shading.light = "STUDIO"
scene.display.shading.show_cavity = True
scene.display.shading.cavity_type = "BOTH"
scene.render.resolution_x, scene.render.resolution_y = 1100, 1000
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w"); scene.world = world
world.color = (0.97, 0.97, 0.98)
cam_data = bpy.data.cameras.new("cam"); cam_data.lens = 60
cam = bpy.data.objects.new("cam", cam_data); bpy.context.collection.objects.link(cam)
scene.camera = cam
target = Vector((17, 35, 48))
views = {"front": (-90, 15), "three": (-50, 22), "side": (0, 12), "back": (140, 25)}
closeups = {"side_close": (0, 8), "back_close": (120, 30), "left_close": (180, 10), "face_close": (-90, 6)}
for name, (az, el) in {**views, **closeups}.items():
    d = 330 if name in views else 170
    target = Vector((17, 35, 48)) if name in views else Vector((17, 0, 60)) if name == "face_close" else Vector((12, 14, 76))
    d = 120 if name == "face_close" else d
    a, e = math.radians(az), math.radians(el)
    cam.location = target + Vector((d * math.cos(e) * math.cos(a), d * math.cos(e) * math.sin(a), d * math.sin(e)))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{OUT}/preview_{name}.png"
    bpy.ops.render.render(write_still=True)
print("DONE")
