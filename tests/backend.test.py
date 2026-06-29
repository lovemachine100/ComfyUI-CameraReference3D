"""Backend tests for CameraReference3D: generate() resample logic (+ optional /meta route).
Run:  python tests/backend.test.py        (uses the ComfyUI embedded python if PIL/torch are needed)
The /meta section is skipped automatically if ComfyUI isn't reachable at $COMFY_URL (default :8188).
"""
import importlib.util, sys, os, json, urllib.request, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
NODE = os.path.join(REPO, "nodes.py")
PREV = os.path.join(REPO, "web", "previews")
COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")

P = F = 0; fails = []
def ok(name, cond, extra=""):
    global P, F
    if cond: P += 1; print("  PASS " + name)
    else: F += 1; fails.append(name + ((" :: " + str(extra)) if extra else "")); print("  FAIL " + name + ((" :: " + str(extra)) if extra else ""))

spec = importlib.util.spec_from_file_location("crn", NODE)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
node = m.CameraReference3D()

# a non-parametric preview name so generate() takes the video branch
src = None
for cand in ("orbit_cw.mp4", "static.mp4"):
    if os.path.exists(os.path.join(PREV, cand)): src = os.path.join(PREV, cand); break
tmp = os.path.join(PREV, "uitest_backend.mp4")
shutil.copyfile(src, tmp)
try:
    print("T2 generate() video mode: frames-controlled resample")
    full, _ = node.generate(motion="uitest_backend", frames=0, width=544, height=960, amount=1.0, hfov=55.0, fps=25.0)
    N = full.shape[0]
    ok("T2.native length>0", N > 0, N)
    o, _ = node.generate(motion="uitest_backend", frames=min(49, N), width=1, height=1, amount=1, hfov=55, fps=25)
    ok("T2.resample to requested", o.shape[0] == min(49, N), o.shape[0])
    o, _ = node.generate(motion="uitest_backend", frames=N + 100, width=1, height=1, amount=1, hfov=55, fps=25)
    ok("T2.frames>native clamps", o.shape[0] == N, o.shape[0])
    o, _ = node.generate(motion="uitest_backend", frames=0, width=1, height=1, amount=1, hfov=55, fps=25)
    ok("T2.frames=0 -> all native", o.shape[0] == N, o.shape[0])
    o, _ = node.generate(motion="uitest_backend", frames="", width=1, height=1, amount=1, hfov=55, fps=25)
    ok("T2.frames='' defensive -> all native", o.shape[0] == N, o.shape[0])
    ok("T2.native resolution preserved", full.shape[1] > 1 and full.shape[2] > 1, tuple(full.shape))

    print("T2b generate() parametric: frames=count, honors width/height")
    p, _ = node.generate(motion="orbit_cw", frames=33, width=128, height=256, amount=1.0, hfov=55.0, fps=24.0)
    ok("T2b.count==frames(33)", p.shape[0] == 33, p.shape[0])
    ok("T2b.res 256x128 (H,W)", p.shape[1] == 256 and p.shape[2] == 128, tuple(p.shape))
finally:
    os.remove(tmp)

print("T3 /meta route (skipped if ComfyUI not running)")
def meta(name):
    r = urllib.request.urlopen(COMFY + "/camera_reference_3d/meta?name=" + name, timeout=10)
    return r.status, json.load(r)
try:
    urllib.request.urlopen(COMFY + "/system_stats", timeout=4)
    reachable = True
except Exception:
    reachable = False
if not reachable:
    print("  SKIP T3 (ComfyUI not reachable at %s)" % COMFY)
else:
    try:
        st, j = meta("orbit_cw")
        ok("T3.meta 200", st == 200, st)
        ok("T3.has w/h/frames/fps", all(k in j for k in ("width", "height", "frames", "fps")), j)
        ok("T3.frames>0", j.get("frames", 0) > 0, j.get("frames"))
        try:
            meta("definitely_nope_xyz"); ok("T3.404 for missing", False, "no error raised")
        except urllib.error.HTTPError as e:
            ok("T3.404 for missing", e.code == 404, e.code)
    except Exception as e:
        ok("T3.meta route", False, repr(e))

print("\n==== BACKEND: %d passed, %d failed ====" % (P, F))
sys.exit(1 if F else 0)
