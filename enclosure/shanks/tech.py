"""The technical part of Shanks' Den Den Mushi: hollow it, open it underneath for the cradle,
and make room for the electronics.

    Blender -b --python tech.py -- <out_dir>

Reads out/shanks.stl (the snail with its hat). Writes:
  body_hollow.stl   walls of WALL mm, open underneath, 4 magnet bosses, IR LED holes in the
                    eyes, IR receiver window and third LED at the front, USB tunnel at the back
  cradle_outline.scad   the floor opening, as an OpenSCAD polygon, for the cradle
The snail looks towards -y, z up, its left flank at +x.
"""
import math
import sys
import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

OUT = sys.argv[-1]
WALL = 2.5            # resin walls
FLOOR_Z = 3.5         # the cradle closes the bottom up to here
CLR = 0.3             # cradle clearance
MAGNET = (6.4, 2.3)   # 6 x 2 mm discs, with clearance
BOSS_R = 4.4
BOSSES = [(2, -1), (2, 1), (36, -1), (36, 1)]          # (y, side): against the walls, clear of the parts
EYES = [(8, 57), (26, 57)]                              # (x, z) of the pupils: 3 mm IR LEDs
LED_D = 3.15
TSOP = (17, 4.0, 6.4, 7.5)                              # window: x centre, z bottom, width, height
LED3 = (25.5, 7.5)                                      # third LED at the front, (x, z)
USB = (17, 6.0, 12.5, 8.5)                              # tunnel: x centre, z centre, width, height


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def load(path, name):
    bpy.ops.wm.stl_import(filepath=path)
    ob = bpy.context.selected_objects[0]; ob.name = name
    return ob


def mesh_object(name, bm):
    me = bpy.data.meshes.new(name); bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me); bpy.context.collection.objects.link(ob)
    return ob


def boolean(target, cutter, op, solver="EXACT"):
    m = target.modifiers.new("b", "BOOLEAN"); m.operation, m.solver, m.object = op, solver, cutter
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=m.name)


def box(name, lo, hi):
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1)
    for v in bm.verts:
        v.co = Vector([lo[i] + (v.co[i] + 0.5) * (hi[i] - lo[i]) for i in range(3)])
    return mesh_object(name, bm)


def cylinder(name, a, b, d, segs=32):
    """A cylinder from point a to point b."""
    a, b = Vector(a), Vector(b)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=d / 2, radius2=d / 2, depth=(b - a).length)
    rot = Vector((0, 0, 1)).rotation_difference((b - a).normalized()).to_matrix().to_4x4()
    bm.transform(Matrix.Translation((a + b) / 2) @ rot)
    return mesh_object(name, bm)


def offset_inside(ob, dist, voxel=0.4):
    """The solid shrunk by dist, through a signed distance grid (exact, no self-crossings)."""
    ng = bpy.data.node_groups.new("inner", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    sdf = ng.nodes.new("GeometryNodeMeshToSDFGrid")
    sdf.inputs["Voxel Size"].default_value, sdf.inputs["Band Width"].default_value = voxel, 12
    off = ng.nodes.new("GeometryNodeSDFGridOffset"); off.inputs["Distance"].default_value = -dist   # negative shrinks
    tom = ng.nodes.new("GeometryNodeGridToMesh")
    for a, b in ((gi.outputs[0], sdf.inputs[0]), (sdf.outputs[0], off.inputs[0]), (off.outputs[0], tom.inputs[0]), (tom.outputs[0], go.inputs[0])):
        ng.links.new(a, b)
    inner = ob.copy(); inner.data = ob.data.copy(); bpy.context.collection.objects.link(inner)
    m = inner.modifiers.new("inner", "NODES"); m.node_group = ng
    bpy.context.view_layer.objects.active = inner
    bpy.ops.object.modifier_apply(modifier=m.name)
    return inner


def section(ob, z):
    """The largest closed outline of ob cut at height z, as a list of (x, y)."""
    bm = bmesh.new(); bm.from_mesh(ob.data)
    res = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, z), plane_no=(0, 0, 1))
    edges = [e for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
    return [(v.co.x, v.co.y) for v in longest_loop(neighbours(edges))]


def neighbours(edges):
    """Each vertex of the cut and the ones an edge joins it to."""
    nxt = {}
    for e in edges:
        a, b = e.verts
        nxt.setdefault(a, []).append(b); nxt.setdefault(b, []).append(a)
    return nxt


def longest_loop(nxt):
    seen, best = set(), []
    for start in nxt:
        if start in seen:
            continue
        loop = walk(nxt, start, seen)
        if len(loop) > len(best):
            best = loop
    return best


def walk(nxt, start, seen):
    """The vertices from start on, edge after edge, until back at start or on a vertex already seen."""
    loop, prev, cur = [start], None, start
    seen.add(start)
    while (nv := step(nxt, prev, cur)) is not None and nv is not start and nv not in seen:
        prev, cur = cur, nv; seen.add(cur); loop.append(cur)
    return loop


def step(nxt, prev, cur):
    """The next vertex after cur, not going back to prev; None at a dead end."""
    cand = [v for v in nxt[cur] if v is not prev]
    return cand[0] if cand else None


def prism(name, outline, z0, z1):
    bm = bmesh.new()
    lo = [bm.verts.new((x, y, z0)) for x, y in outline]; hi = [bm.verts.new((x, y, z1)) for x, y in outline]
    bm.faces.new(lo[::-1]); bm.faces.new(hi)
    n = len(outline)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_object(name, bm)


reset()
snail = load(f"{OUT}/shanks.stl", "snail")
# close the small holes the booleans left (692 open edges): the distance grids need a watertight solid
# the booleans that made the snail left fins and inner faces (686 non-manifold edges): clean them,
# then remesh, so the distance grids get a watertight solid
bpy.context.view_layer.objects.active = snail
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.remove_doubles(threshold=1e-4)
bpy.ops.mesh.dissolve_degenerate(threshold=1e-3)
bpy.ops.mesh.select_all(action="DESELECT"); bpy.ops.mesh.select_interior_faces(); bpy.ops.mesh.delete(type="FACE")
bpy.ops.mesh.select_all(action="DESELECT")
bpy.ops.mesh.select_non_manifold(extend=False, use_wire=True, use_boundary=False, use_multi_face=True, use_non_contiguous=False, use_verts=False)
bpy.ops.mesh.delete(type="FACE")
bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.fill_holes(sides=0); bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode="OBJECT")
m = snail.modifiers.new("close", "REMESH"); m.mode, m.voxel_size = "VOXEL", 0.3
bpy.ops.object.modifier_apply(modifier=m.name)
_bm = bmesh.new(); _bm.from_mesh(snail.data)
print("CLOSED open edges", sum(1 for e in _bm.edges if not e.is_manifold), "volume %.1f ml" % (_bm.calc_volume() / 1000))
tree = BVHTree.FromObject(snail, bpy.context.evaluated_depsgraph_get())
inner = offset_inside(snail, WALL)
inner_tree = BVHTree.FromObject(inner, bpy.context.evaluated_depsgraph_get())

# the opening underneath: the cavity's outline at FLOOR_Z, all the way down
outline = section(inner, FLOOR_Z)
print("OUTLINE points", len(outline))
opening = prism("opening", outline, -1, FLOOR_Z + 0.5)

# magnet bosses: cylinders standing on the floor line, half in the wall
boss_parts = []
for y, side in BOSSES:
    hit = inner_tree.ray_cast(Vector((17 + side * 80, y, 8)), Vector((-side, 0, 0)))
    x = hit[0].x - side * (BOSS_R - 2.2)            # 2.2 mm of the boss inside the wall
    boss_parts.append((x, y, cylinder("boss", (x, y, FLOOR_Z), (x, y, FLOOR_Z + 12), 2 * BOSS_R)))
print("BOSSES", [(round(x, 1), y) for x, y, _ in boss_parts])
bpy.data.objects.remove(inner)

# what goes through the walls: magnet pockets, the eyes, the IR window and third LED, the USB tunnel
cut_parts = []
for x, y, _ in boss_parts:
    cut_parts.append(cylinder("mag", (x, y, FLOOR_Z - 0.1), (x, y, FLOOR_Z + MAGNET[1]), MAGNET[0]))
for x, z in EYES:
    cut_parts.append(cylinder("eye", (x, -40, z), (x, 10, z), LED_D, segs=24))
tx, tz, tw, th = TSOP
cut_parts.append(box("tsop", (tx - tw / 2, -40, tz), (tx + tw / 2, 0, tz + th)))
cut_parts.append(cylinder("led3", (LED3[0], -40, LED3[1]), (LED3[0], 0, LED3[1]), LED_D, segs=24))
ux, uz, uw, uh = USB
cut_parts.append(box("usb", (ux - uw / 2, 67, uz - uh / 2), (ux + uw / 2, 130, uz + uh / 2)))


def join(name, obs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    bpy.ops.object.join()
    obs[0].name = name
    return obs[0]


bosses_ob = join("bosses", [b for _, _, b in boss_parts])
cuts_ob = join("cuts", cut_parts)

# all of it in signed distance grids: the result is always a closed, printable solid
VOXEL = 0.25
ng = bpy.data.node_groups.new("hollow", "GeometryNodeTree")
ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
N, L = ng.nodes.new, ng.links.new
gi, go = N("NodeGroupInput"), N("NodeGroupOutput")


def sdf_of(geom_socket):
    n = N("GeometryNodeMeshToSDFGrid")
    n.inputs["Voxel Size"].default_value, n.inputs["Band Width"].default_value = VOXEL, 14
    L(geom_socket, n.inputs["Mesh"])
    return n.outputs[0]


def obj(ob):
    n = N("GeometryNodeObjectInfo"); n.inputs["Object"].default_value = ob
    n.transform_space = "RELATIVE"
    return n.outputs["Geometry"]


def bool_(op, a, bs):
    """DIFFERENCE: a minus all of bs. UNION / INTERSECT: of a and all of bs (one multi-input)."""
    n = N("GeometryNodeSDFGridBoolean"); n.operation = op
    sock = {s.identifier: s for s in n.inputs}
    if op == "DIFFERENCE":
        L(a, sock["Grid 1"])
    else:
        bs = [a] + list(bs)
    for g in bs:
        L(g, sock["Grid 2"])
    return n.outputs[0]


S = sdf_of(gi.outputs[0])
off = N("GeometryNodeSDFGridOffset"); off.inputs["Distance"].default_value = -WALL; L(S, off.inputs["Grid"])
cavity = bool_("DIFFERENCE", bool_("UNION", off.outputs[0], [sdf_of(obj(opening))]), [sdf_of(obj(bosses_ob))])
shell = bool_("DIFFERENCE", S, [cavity, sdf_of(obj(cuts_ob))])
import os
stage = os.environ.get("STAGE", "shell")
pick = {"S": S, "inner": off.outputs[0], "cavity": cavity, "shell": shell,
        "s_minus_inner": bool_("DIFFERENCE", S, [off.outputs[0]]),
        "opening": sdf_of(obj(opening)), "cuts": sdf_of(obj(cuts_ob)), "bosses": sdf_of(obj(bosses_ob))}[stage]
tom = N("GeometryNodeGridToMesh"); L(pick, tom.inputs["Grid"]); L(tom.outputs[0], go.inputs[0])
m = snail.modifiers.new("hollow", "NODES"); m.node_group = ng
bpy.context.view_layer.objects.active = snail
bpy.ops.object.modifier_apply(modifier=m.name)
print("STAGE", stage, "verts", len(snail.data.vertices))
body = snail
for o in (opening, bosses_ob, cuts_ob):
    bpy.data.objects.remove(o)
bosses = boss_parts

# keep the snail only (a stray sliver would print as debris)
bpy.ops.object.select_all(action="DESELECT"); body.select_set(True); bpy.context.view_layer.objects.active = body
bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.separate(type="LOOSE"); bpy.ops.object.mode_set(mode="OBJECT")
pieces = sorted(bpy.context.selected_objects, key=lambda o: len(o.data.vertices), reverse=True)
print("PIECES", [len(o.data.vertices) for o in pieces[:5]])
body = pieces[0]
for o in pieces[1:]:
    bpy.data.objects.remove(o)
bm = bmesh.new(); bm.from_mesh(body.data)
print("VOLUME %.1f ml" % (bm.calc_volume() / 1000))

bpy.ops.object.select_all(action="DESELECT"); body.select_set(True)
bpy.ops.wm.stl_export(filepath=f"{OUT}/body_hollow.stl", export_selected_objects=True)

# the cradle's outline (the opening, less the clearance comes in OpenSCAD) and the boss positions
with open(f"{OUT}/cradle_outline.scad", "w") as f:
    f.write("// generated by tech.py: the floor opening of the hollow snail, and its magnet bosses\n")
    f.write(f"floor_z = {FLOOR_Z};\n")
    f.write("opening = [" + ", ".join(f"[{x:.2f}, {y:.2f}]" for x, y in outline) + "];\n")
    f.write("bosses = [" + ", ".join(f"[{x:.2f}, {y:.2f}]" for x, y, _ in bosses) + "];\n")
print("DONE")
