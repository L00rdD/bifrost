"""Render Shanks' Den Den Mushi with its parts, painted roughly as they will be.

    Blender -b --python render_all.py -- <out_dir> [name=az,el,dist,tx,ty,tz ...]
"""
import math
import os
import sys
import bpy
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
PARTS = {"shanks": (0.93, 0.80, 0.62), "cape": (0.10, 0.10, 0.12), "sword": (0.15, 0.13, 0.12), "hat": (0.93, 0.78, 0.36)}
for name, rgb in PARTS.items():
    path = f"{OUT}/{name}.stl"
    if name == "hat" or not os.path.exists(path):
        continue
    bpy.ops.wm.stl_import(filepath=path)
    ob = bpy.context.selected_objects[0]
    ob.color = (*rgb, 1)
# the hat over the merged snail, in its own colour (same geometry, drawn on top)
if os.path.exists(f"{OUT}/hat.stl"):
    bpy.ops.wm.stl_import(filepath=f"{OUT}/hat.stl")
    hat = bpy.context.selected_objects[0]; hat.color = (*PARTS["hat"], 1)
    hat.scale = (1.002, 1.002, 1.002)
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.color_type = "OBJECT"
scene.display.shading.light = "STUDIO"
scene.display.shading.show_cavity = True
scene.display.shading.cavity_type = "BOTH"
scene.display.shading.show_shadows = True
scene.view_settings.view_transform = "Standard"
scene.render.resolution_x, scene.render.resolution_y = 1000, 900
world = bpy.data.worlds.new("w"); scene.world = world; world.color = (0.97, 0.97, 0.98)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); cam.data.lens = 60
bpy.context.collection.objects.link(cam); scene.camera = cam
views = {"three": "-50,22,330,17,35,50", "left": "0,12,330,17,35,50", "back": "140,30,330,17,35,50",
         "right": "180,15,330,17,35,50", "front": "-90,12,300,17,35,50", "sword": "-20,15,200,40,35,30"}
for arg in sys.argv[sys.argv.index("--") + 2:]:
    k, v = arg.split("="); views[k] = v
for name, spec in views.items():
    az, el, d, tx, ty, tz = map(float, spec.split(","))
    a, e = math.radians(az), math.radians(el)
    target = Vector((tx, ty, tz))
    cam.location = target + d * Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = f"{OUT}/all_{name}.png"
    bpy.ops.render.render(write_still=True)
print("DONE")
