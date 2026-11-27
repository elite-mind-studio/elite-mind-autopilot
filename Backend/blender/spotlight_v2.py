"""
Blender scene (Blender 4.2), v2: "The spotlight effect" with a real rigged character.

Black is Quaternius' "Business Man" (CC0, poly.pizza) recoloured: dark brown skin, black suit,
gold tie. He walks in under a spotlight that follows him; the crowd around him (recoloured copies,
idling, facing away) never looks at him. The model is downloaded at render time.

    blender -b -P spotlight_v2.py -- <stills|video> <out_dir> [width height seconds samples]
"""
import math
import re
import sys
import urllib.request
from pathlib import Path

import bpy
from mathutils import Vector

MODEL_URL = "https://static.poly.pizza/e599abbe-7d73-488c-9d7e-3ead281e705c.glb"  # Business Man, CC0
# On Kaggle the model comes from the private dataset elite-3d-assets (the host blocks cloud downloads)
MODEL = next(Path("/kaggle/input").rglob("businessman.glb"), None) if Path("/kaggle/input").exists() else None
MODEL = MODEL or (Path("/tmp/businessman.glb") if Path("/tmp").exists() else Path.home() / "businessman.glb")

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["stills", "out"]
MODE, OUT = args[0], Path(args[1])
WIDTH, HEIGHT = (int(args[2]), int(args[3])) if len(args) > 3 else (1080, 1920)
SECONDS = float(args[4]) if len(args) > 4 else 10
SAMPLES = int(args[5]) if len(args) > 5 else 64
FPS = 30
FRAMES = int(SECONDS * FPS)
WALK_END = int(FRAMES * 0.6)

if not MODEL.exists():  # the host refuses Python's default user agent
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    MODEL.write_bytes(urllib.request.urlopen(request, timeout=120).read())

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, FRAMES


def recolor(objects, colors):
    """colors: {material name prefix: (r, g, b, roughness, metallic)}; materials are copied per figure."""
    for obj in objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            if not slot.material:
                continue
            base = slot.material.name.split(".")[0]
            if base in colors:
                mat = slot.material.copy()
                slot.material = mat
                r, g, b, rough, metal = colors[base]
                bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
                if bsdf:
                    for link in list(bsdf.inputs["Base Color"].links):
                        mat.node_tree.links.remove(link)  # drop any texture so the colour applies
                    bsdf.inputs["Base Color"].default_value = (r, g, b, 1)
                    bsdf.inputs["Roughness"].default_value = rough
                    bsdf.inputs["Metallic"].default_value = metal


def action(name):
    """The glTF importer names actions like "CharacterArmature|Walk", sometimes with the object name
    or a .001 appended ("CharacterArmature|Walk_CharacterArmature.001"); "Idle" must not match "Idle_Gun"."""
    pattern = re.compile(rf"^(.*\|)?{re.escape(name)}(_CharacterArmature[^|]*|_Armature[^|]*)?(\.\d+)?$")
    found = [a for a in bpy.data.actions if pattern.match(a.name)]
    if not found:
        raise RuntimeError(f"No action {name!r}; actions: {[a.name for a in bpy.data.actions][:30]}")
    return found[0]


def play(armature, clip, start, end, track_name="anim", blend_in=0):
    """Loop an action from frame `start` to `end` on its own NLA track."""
    act = action(clip)
    track = armature.animation_data.nla_tracks.new()
    track.name = track_name
    strip = track.strips.new(clip, int(start), act)
    length = act.frame_range[1] - act.frame_range[0]
    strip.repeat = max(1.0, (end - start) / max(1.0, length))
    strip.blend_in = blend_in
    return strip


def figure(name, loc, yaw, colors, height=1.8):
    """Import the character, scale it to `height` metres, recolour; returns the armature."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(MODEL))
    new = [o for o in bpy.data.objects if o not in before]
    armature = next(o for o in new if o.type == "ARMATURE")
    root = armature
    while root.parent:
        root = root.parent
    bpy.context.view_layer.update()
    zs = [(o.matrix_world @ Vector(c)).z for o in new if o.type == "MESH" for c in o.bound_box]
    scale = height / max(0.01, max(zs) - min(zs))
    root.scale = (scale, scale, scale)
    root.location = loc
    root.rotation_mode = "XYZ"
    root.rotation_euler = (0, 0, yaw)
    root.name = name
    recolor(new, colors)
    if armature.animation_data is None:
        armature.animation_data_create()
    armature.animation_data.action = None  # animations come from NLA strips
    for track in list(armature.animation_data.nla_tracks):
        armature.animation_data.nla_tracks.remove(track)
    return root, armature


# ---------- set ----------
floor_mat = bpy.data.materials.new("floor")
floor_mat.use_nodes = True
fb = floor_mat.node_tree.nodes["Principled BSDF"]
fb.inputs["Base Color"].default_value = (0.02, 0.02, 0.025, 1)
fb.inputs["Roughness"].default_value = 0.3
bpy.ops.mesh.primitive_plane_add(size=80)
bpy.context.object.data.materials.append(floor_mat)

world = bpy.data.worlds.new("world")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.004, 0.005, 0.012, 1)

BLACK_COLORS = {"Skin": (0.16, 0.075, 0.04, 0.5, 0), "Suit": (0.012, 0.012, 0.014, 0.45, 0),
                "Tie": (1.0, 0.7, 0.2, 0.25, 1.0), "Hair": (0.01, 0.01, 0.01, 0.7, 0)}
black, black_arm = figure("Black", (0, 7, 0), 0, BLACK_COLORS)  # faces -Y: toward the camera
play(black_arm, "Walk", 1, WALK_END, "walk")
play(black_arm, "Idle", WALK_END, FRAMES + 30, "idle", blend_in=8)
for frame in range(1, FRAMES + 1):
    t = min(frame, WALK_END) / WALK_END
    black.location = (0, 7 - 6.4 * (1 - (1 - t) ** 1.25), 0)
    black.keyframe_insert("location", frame=frame)

suits = [(0.22, 0.24, 0.3), (0.3, 0.3, 0.32), (0.18, 0.2, 0.22), (0.35, 0.33, 0.3)]
skins = [(0.55, 0.38, 0.28), (0.3, 0.17, 0.1), (0.7, 0.52, 0.4), (0.22, 0.12, 0.07)]
crowd_spots = [(-2.4, 1.0, 120), (2.6, 0.4, -110), (-1.5, 3.0, 170), (1.8, 3.2, -160), (-3.6, -1.2, 95),
               (3.5, -1.8, -80), (-1.1, 5.9, 200), (-3.0, 4.7, 140), (3.2, 4.6, -150)]
for n, (x, y, yaw) in enumerate(crowd_spots):
    colors = {"Skin": (*skins[n % 4], 0.5, 0), "Suit": (*suits[n % 4], 0.6, 0),
              "Tie": (0.25, 0.05, 0.06, 0.5, 0), "Hair": (0.03, 0.02, 0.015, 0.7, 0)}
    _, arm = figure(f"crowd{n}", (x, y, 0), math.radians(yaw), colors, height=1.72 + 0.04 * (n % 3))
    strip = play(arm, "Idle_Neutral" if n % 2 else "Idle", 1, FRAMES + 30)
    strip.frame_start = 1 - (n * 7) % 25  # out of step, so they don't sway together

target = bpy.data.objects.new("target", None)
scene.collection.objects.link(target)
target.parent = black
target.location = (0, 0, 1.25 / black.scale[0])

# ---------- lights ----------
bpy.ops.object.light_add(type="SPOT", location=(0, -1.2, 9))
spot = bpy.context.object
spot.data.energy = 9000
spot.data.spot_size = math.radians(24)
spot.data.spot_blend = 0.4
spot.data.color = (1.0, 0.86, 0.62)
spot.constraints.new("TRACK_TO").target = target

beam = bpy.data.materials.new("beam")
beam.use_nodes = True
nodes, links = beam.node_tree.nodes, beam.node_tree.links
nodes.clear()
out_node = nodes.new("ShaderNodeOutputMaterial")
mix = nodes.new("ShaderNodeMixShader")
mix.inputs["Fac"].default_value = 0.012  # a soft haze, not a solid cone
transparent = nodes.new("ShaderNodeBsdfTransparent")
glow = nodes.new("ShaderNodeEmission")
glow.inputs["Color"].default_value = (1.0, 0.85, 0.6, 1)
glow.inputs["Strength"].default_value = 2.0
links.new(transparent.outputs[0], mix.inputs[1])
links.new(glow.outputs[0], mix.inputs[2])
links.new(mix.outputs[0], out_node.inputs["Surface"])
holder = bpy.data.objects.new("beam_holder", None)
scene.collection.objects.link(holder)
holder.constraints.new("COPY_LOCATION").target = black
holder.constraints[-1].use_z = False
bpy.ops.mesh.primitive_cone_add(vertices=64, radius1=1.9, radius2=0.08, depth=9, location=(0, 0, 4.5))
cone = bpy.context.object
cone.data.materials.append(beam)
cone.parent = holder
cone.visible_shadow = False

bpy.ops.object.light_add(type="AREA", location=(0, 2, 7))
fill = bpy.context.object
fill.data.energy = 320
fill.data.size = 14
fill.data.color = (0.45, 0.55, 1.0)

# ---------- camera: wide, then a slow push-in on Black (9:16) ----------
bpy.ops.object.camera_add(location=(2.4, -12, 3.2))
cam = bpy.context.object
cam.data.lens = 35
scene.camera = cam
cam.constraints.new("TRACK_TO").target = target
cam.keyframe_insert("location", frame=1)
cam.location = (0.9, -2.6, 1.75)
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
