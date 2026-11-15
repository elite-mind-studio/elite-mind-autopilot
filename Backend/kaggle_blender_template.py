# Kaggle GPU job: download Blender, render the embedded scene script, save the results to out/.
# Filled in by Backend/kaggle_blender.py (__SCENE__ = base64 scene script, __RUNS__ = argument lists).
import base64
import json
import subprocess
from pathlib import Path

BLENDER_URL = "https://download.blender.org/release/Blender4.2/blender-4.2.3-linux-x64.tar.xz"
OUT = Path("/kaggle/working/out")
OUT.mkdir(parents=True, exist_ok=True)
log = open(OUT / "render.log", "w")


def run(cmd, **kw):
    print("$", " ".join(cmd), flush=True)
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()
    return subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, **kw)


# Libraries Blender needs on a headless Linux box (most are already there)
run(["bash", "-c", "apt-get -qq update && apt-get -qq install -y libxi6 libxkbcommon0 libxrender1 libxxf86vm1 "
     "libxfixes3 libsm6 libgl1 libegl1 > /dev/null 2>&1 || true"])
run(["wget", "-q", BLENDER_URL, "-O", "/tmp/blender.tar.xz"], check=True)
run(["tar", "-xf", "/tmp/blender.tar.xz", "-C", "/tmp"], check=True)
blender = next(Path("/tmp").glob("blender-*-linux-x64")) / "blender"

scene = Path("/tmp/scene.py")
scene.write_bytes(base64.b64decode("__SCENE__"))
for args in json.loads('__RUNS__'):
    # scene arguments: <mode> <out_dir> [width height seconds samples]
    result = run([str(blender), "-b", "-P", str(scene), "--", args[0], str(OUT), *args[1:]])
    print(f"render {args} -> exit {result.returncode}", flush=True)
log.close()
print((OUT / "render.log").read_text()[-3000:])
