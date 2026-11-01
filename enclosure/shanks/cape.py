"""Shanks' cape, laid on the snail's back: it follows the shell from the neck (just behind the
brim) over the top of the shell and a little down its sides, with a wavy hem and folds that
fan out from the neck. Deterministic: the snail's own surface, lifted and thickened.

    Blender -b --python cape.py -- <shanks.stl> <out_dir>

Writes cape.stl (a separate part, 1.6 mm thick, to paint black) and cape_*.png.
"""
import math
import sys
import bpy
import bmesh
from mathutils import Vector

SNAIL, OUT = sys.argv[-2], sys.argv[-1]
NECK = Vector((17, 17, 79))   # where the cape hangs from: behind the head, under the back of the brim
LENGTH = 60                   # how far it reaches from the neck (the sides count double: see reach())
HAT = Vector((17, 4, 0)); HAT_R = 27   # keep clear of the hat: nothing within this radius above HAT_Z
HAT_Z = 71
GAP, THICK = 1.8, 1.6         # off the snail (over the dial and the medallion), and the cloth's thickness
FOLDS, FOLD_DEPTH = 7, 2.5    # folds fanning out from the neck


def reach(p):
    """Distance from the neck, the height counting double: the cape goes further over the top
    of the shell than down its sides, so the dial and the medallion stay in view."""
    d = p - NECK
    return math.sqrt(d.x ** 2 + d.y ** 2 + (2.0 * d.z) ** 2)


def azimuth(p):
    return math.atan2(p.x - NECK.x, p.y - NECK.y)


def hem(p):
    back = min(1.0, max(0.0, (p.y - 45) / 40))           # longer down the back
    return LENGTH + 22 * back + 7.0 * math.sin(FOLDS * azimuth(p))   # the hem dips in each fold


bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=SNAIL)
snail = bpy.context.selected_objects[0]

# the cloth does not follow the small relief (dial, medallion, seams): drape it on a coarse,
# smoothed copy of the snail
m = snail.modifiers.new("coarse", "REMESH"); m.mode, m.voxel_size = "VOXEL", 1.5
s2 = snail.modifiers.new("soft", "SMOOTH"); s2.factor, s2.iterations = 1.0, 8
dg = bpy.context.evaluated_depsgraph_get()
bm = bmesh.new()
bm.from_mesh(snail.evaluated_get(dg).to_mesh())
snail.modifiers.clear()
bm.normal_update()
keep = set()
for f in bm.faces:
    c = f.calc_center_median()
    if c.y < NECK.y - 1 or f.normal.z < -0.35:                   # behind the head only, no undersides
        continue
    if c.z > HAT_Z and math.hypot(c.x - HAT.x, c.y - HAT.y) < HAT_R:   # not on the hat
        continue
    if abs(f.normal.x) > 0.55 and c.z < 72:                 # the flat sides: stay above the dial and the medallion
        continue
    if reach(c) < hem(c):
        keep.add(f)
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f not in keep], context="FACES")
# keep the biggest connected patch (stray faces elsewhere would print as debris)
bm.faces.ensure_lookup_table()
seen, best = set(), []
for f in bm.faces:
    if f in seen:
        continue
    stack, comp = [f], []
    seen.add(f)
    while stack:
        g = stack.pop(); comp.append(g)
        for e in g.edges:
            for h in e.link_faces:
                if h not in seen:
                    seen.add(h); stack.append(h)
    if len(comp) > len(best):
        best = comp
bmesh.ops.delete(bm, geom=[f for f in bm.faces if f not in set(best)], context="FACES")
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
bm.normal_update()
me0 = bpy.data.meshes.new("patch"); bm.to_mesh(me0)
patch = bpy.data.objects.new("patch", me0); bpy.context.collection.objects.link(patch)
bpy.context.view_layer.objects.active = patch
m = patch.modifiers.new("smooth", "SMOOTH"); m.factor, m.iterations = 0.8, 6
bpy.ops.object.modifier_apply(modifier=m.name)
bm = bmesh.new(); bm.from_mesh(patch.data); bm.normal_update()
bpy.data.objects.remove(patch)
# lift off the snail, with folds that deepen towards the hem
for v in bm.verts:
    t = min(1.0, reach(v.co) / LENGTH)
    fold = FOLD_DEPTH * t * t * (0.5 + 0.5 * math.sin(FOLDS * azimuth(v.co)))
    v.co += v.normal * (GAP + fold)
me = bpy.data.meshes.new("cape"); bm.to_mesh(me)
cape = bpy.data.objects.new("cape", me); bpy.context.collection.objects.link(cape)
bpy.context.view_layer.objects.active = cape
for kind, setup in (("SOLIDIFY", lambda m: (setattr(m, "thickness", THICK), setattr(m, "offset", 1))),):
    m = cape.modifiers.new(kind.lower(), kind); setup(m)
    bpy.ops.object.modifier_apply(modifier=m.name)
print("CAPE faces", len(cape.data.polygons))

bpy.ops.object.select_all(action="DESELECT"); cape.select_set(True)
bpy.ops.wm.stl_export(filepath=f"{OUT}/cape.stl", export_selected_objects=True)

# previews
scene = bpy.context.scene
snail.color = (0.93, 0.80, 0.62, 1)
cape.color = (0.12, 0.12, 0.14, 1)
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.color_type = "OBJECT"
scene.display.shading.show_cavity = True
scene.view_settings.view_transform = "Standard"
scene.render.resolution_x, scene.render.resolution_y = 1000, 900
world = bpy.data.worlds.new("w"); scene.world = world; world.color = (0.97, 0.97, 0.98)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); cam.data.lens = 60
bpy.context.collection.objects.link(cam); scene.camera = cam
target = Vector((17, 35, 50))
for name, (az, el) in {"three": (-50, 22), "side": (0, 12), "back": (140, 30), "other": (180, 15)}.items():
    a, e = math.radians(az), math.radians(el)
    cam.location = target + 330 * Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{OUT}/cape_{name}.png"
    bpy.ops.render.render(write_still=True)
print("DONE")
