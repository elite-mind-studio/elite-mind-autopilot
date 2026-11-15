"""
Blender scene (runs inside Blender 4.2): "The spotlight effect" test for Elite Mind.

Black walks into a dark room under a spotlight that follows him, while everyone around him
looks at their phones. Everything is built from primitives in code (no downloaded assets).

    blender -b -P spotlight_test.py -- <stills|video> <out_dir> [width height seconds samples]
"""
import math
import sys
from pathlib import Path

import bpy

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["stills", "out"]
MODE, OUT = args[0], Path(args[1])
WIDTH, HEIGHT = (int(args[2]), int(args[3])) if len(args) > 3 else (1080, 1920)
SECONDS = float(args[4]) if len(args) > 4 else 10
SAMPLES = int(args[5]) if len(args) > 5 else 64
FPS = 30
FRAMES = int(SECONDS * FPS)
WALK_END = int(FRAMES * 0.6)  # Black stops under the light at 60% of the clip

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


# ---------- materials ----------
def material(name, color, rough=0.5, metal=0.0, emit=None, strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if emit:
        bsdf.inputs["Emission Color"].default_value = (*emit, 1)
        bsdf.inputs["Emission Strength"].default_value = strength
    return mat


SKIN = material("skin", (0.18, 0.09, 0.05), 0.55)
HAIR = material("hair", (0.01, 0.01, 0.01), 0.8)
SUIT = material("suit", (0.012, 0.012, 0.014), 0.45)
GOLD = material("gold", (1.0, 0.72, 0.25), 0.25, 1.0)
GREY = material("crowd", (0.32, 0.33, 0.36), 0.6)
PHONE = material("phone", (0.02, 0.05, 0.1), 0.3, emit=(0.35, 0.65, 1.0), strength=6)
FLOOR = material("floor", (0.018, 0.018, 0.022), 0.3)

beam = bpy.data.materials.new("beam")
beam.use_nodes = True
nodes, links = beam.node_tree.nodes, beam.node_tree.links
nodes.clear()
out_node = nodes.new("ShaderNodeOutputMaterial")
mix = nodes.new("ShaderNodeMixShader")
mix.inputs["Fac"].default_value = 0.06
transparent = nodes.new("ShaderNodeBsdfTransparent")
glow = nodes.new("ShaderNodeEmission")
glow.inputs["Color"].default_value = (1.0, 0.85, 0.6, 1)
glow.inputs["Strength"].default_value = 3.0
links.new(transparent.outputs[0], mix.inputs[1])
links.new(glow.outputs[0], mix.inputs[2])
links.new(mix.outputs[0], out_node.inputs["Surface"])


# ---------- helpers ----------
def smooth(obj, levels=2):
    obj.modifiers.new("subd", "SUBSURF").levels = levels
    obj.modifiers["subd"].render_levels = levels
    for poly in obj.data.polygons:
        poly.use_smooth = True


def part(kind, mat, parent, loc, scale=(1, 1, 1), rot=(0, 0, 0), **kw):
    if kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, **kw)
    elif kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, **kw)
    elif kind == "cube":
        bpy.ops.mesh.primitive_cube_add(**kw)
    elif kind == "torus":
        bpy.ops.mesh.primitive_torus_add(**kw)
    obj = bpy.context.object
    obj.location, obj.scale, obj.rotation_euler = loc, scale, rot
    obj.data.materials.append(mat)
    if kind in ("sphere", "torus"):
        for poly in obj.data.polygons:
            poly.use_smooth = True
    obj.parent = parent
    return obj


def pivot(name, parent, loc):
    empty = bpy.data.objects.new(name, None)
    scene.collection.objects.link(empty)
    empty.parent, empty.location = parent, loc
    return empty


def person(name, loc, yaw, body, hero=False, phone=False):
    """A stylized figure. Pivots: hips/shoulders, so walking is just rotating them."""
    root = pivot(name, None, loc)
    root.rotation_euler = (0, 0, yaw)
    part("sphere", body, root, (0, 0, 1.12), (0.25, 0.17, 0.36), radius=1)  # torso
    head_mat = SKIN if hero else body
    part("sphere", head_mat, root, (0, 0, 1.64), (0.165, 0.165, 0.18), radius=1)
    if hero:
        part("sphere", HAIR, root, (0, -0.015, 1.71), (0.17, 0.17, 0.11), radius=1)  # short hair
        part("sphere", HAIR, root, (0, 0.07, 1.555), (0.12, 0.08, 0.075), radius=1)  # neat beard
        part("cube", GOLD, root, (0, 0.165, 1.2), (0.035, 0.01, 0.17), size=1 * 2)  # gold tie
    limbs = {}
    for side, x in (("L", -0.11), ("R", 0.11)):
        hip = pivot(f"{name}_hip{side}", root, (x, 0, 0.86))
        part("cyl", body, hip, (0, 0, -0.42), (0.075, 0.075, 0.42), radius=1, depth=2)
        part("sphere", HAIR if hero else body, hip, (0, 0.05, -0.86), (0.08, 0.13, 0.05), radius=1)  # shoe
        limbs[f"hip{side}"] = hip
    for side, x in (("L", -0.3), ("R", 0.3)):
        shoulder = pivot(f"{name}_shoulder{side}", root, (x, 0, 1.38))
        part("cyl", body, shoulder, (0, 0, -0.3), (0.06, 0.06, 0.3), radius=1, depth=2)
        part("sphere", SKIN if hero else body, shoulder, (0, 0, -0.63), (0.06, 0.06, 0.06), radius=1)
        if hero and side == "L":
            part("torus", GOLD, shoulder, (0, 0, -0.56), (1, 1, 1),
                 major_radius=0.065, minor_radius=0.018)  # gold watch
        limbs[f"shoulder{side}"] = shoulder
    if phone:  # arms bent forward, a glowing phone in front of the chest
        for side in ("L", "R"):
            limbs[f"shoulder{side}"].rotation_euler = (math.radians(55), 0, 0)
        part("cube", PHONE, root, (0, 0.5, 1.02), (0.05, 0.005, 0.09), (math.radians(-35), 0, 0), size=2)
        root.rotation_euler = (math.radians(-4), 0, yaw)  # head down to the screen
    return root, limbs


# ---------- set ----------
bpy.ops.mesh.primitive_plane_add(size=80)
bpy.context.object.data.materials.append(FLOOR)

world = bpy.data.worlds.new("world")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.004, 0.005, 0.012, 1)

black, limbs = person("Black", (0, 7, 0), math.pi, SUIT, hero=True)  # walks toward the camera
crowd_spots = [(-2.3, 1.0, 40), (2.5, 0.5, -60), (-1.4, 2.9, 160), (1.7, 3.1, -150), (-3.5, -1.1, 80),
               (3.4, -1.7, -100), (-1.0, 5.7, 190), (-2.9, 4.6, 120), (3.1, 4.5, -130), (-4.4, 2.2, 95)]
for n, (x, y, yaw) in enumerate(crowd_spots):
    person(f"crowd{n}", (x, y, 0), math.radians(yaw), GREY, phone=True)

# Black walks in, then stops under the light; a hip/shoulder swing makes the walk
for frame in range(1, FRAMES + 1):
    t = min(frame, WALK_END) / WALK_END
    black.location = (0, 7 - 6.4 * (1 - (1 - t) ** 1.6), 0)
    black.keyframe_insert("location", frame=frame)
    swing = math.sin(frame / FPS * 2 * math.pi * 1.1) * (1 if frame < WALK_END else 0)
    for name, sign in (("hipL", 1), ("hipR", -1), ("shoulderL", -0.8), ("shoulderR", 0.8)):
        limbs[name].rotation_euler = (math.radians(26) * swing * sign, 0, 0)
        limbs[name].keyframe_insert("rotation_euler", frame=frame)

target = pivot("target", black, (0, 0, 1.25))

# The spotlight that follows him, plus a faint visible beam
bpy.ops.object.light_add(type="SPOT", location=(0, -1.2, 9))
spot = bpy.context.object
spot.data.energy = 9000
spot.data.spot_size = math.radians(24)
spot.data.spot_blend = 0.35
spot.data.color = (1.0, 0.86, 0.62)
spot.constraints.new("TRACK_TO").target = target

beam_holder = pivot("beam_holder", None, (0, 0, 0))
beam_holder.constraints.new("COPY_LOCATION").target = black
beam_holder.constraints[-1].use_z = False
bpy.ops.mesh.primitive_cone_add(vertices=64, radius1=1.9, radius2=0.08, depth=9, location=(0, 0, 4.5))
cone = bpy.context.object
cone.data.materials.append(beam)
cone.parent = beam_holder
cone.visible_shadow = False

# Dim blue fill so the crowd reads as silhouettes
bpy.ops.object.light_add(type="AREA", location=(0, 2, 7))
fill = bpy.context.object
fill.data.energy = 220
fill.data.size = 14
fill.data.color = (0.45, 0.55, 1.0)

# Camera: wide, then a slow push-in on Black (9:16)
bpy.ops.object.camera_add(location=(2.6, -13, 3.4))
cam = bpy.context.object
cam.data.lens = 32
scene.camera = cam
cam.constraints.new("TRACK_TO").target = target
cam.keyframe_insert("location", frame=1)
cam.location = (1.1, -3.6, 1.95)
cam.keyframe_insert("location", frame=FRAMES)

# ---------- render ----------
scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
for backend in ("OPTIX", "CUDA"):
    try:
        prefs.compute_device_type = backend
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for device in prefs.devices:
                device.use = device.type == backend
            scene.cycles.device = "GPU"
            print(f"[+] Rendering on {backend}: {[d.name for d in gpus]}", flush=True)
            break
    except TypeError:
        continue
scene.cycles.samples = SAMPLES
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 6
scene.render.resolution_x, scene.render.resolution_y = WIDTH, HEIGHT
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, FRAMES
scene.view_settings.view_transform = "AgX"

OUT.mkdir(parents=True, exist_ok=True)
if MODE == "stills":
    for frame in (int(FRAMES * 0.15), int(FRAMES * 0.5), FRAMES - 5):
        scene.frame_set(frame)
        scene.render.filepath = str(OUT / f"still_{frame:04d}.png")
        bpy.ops.render.render(write_still=True)
else:
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.ffmpeg.constant_rate_factor = "HIGH"
    scene.render.filepath = str(OUT / "scene.mp4")
    bpy.ops.render.render(animation=True)
print("[+] Blender done", flush=True)
