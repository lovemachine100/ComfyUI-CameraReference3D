# -*- coding: utf-8 -*-
"""
ComfyUI-B03-CameraReference

中立な 3D シーン(柱の回廊+床+天井)に、選んだカメラ動作を正確に当てて
「カメラ動作 参照フレーム列(IMAGE バッチ)」をその場で生成するノード。
出力を LTXAddVideoICLoRAGuide の image 入力に直結すると、Cameraman IC-LoRA が
そのカメラの動きだけを生成へ転写する(Mode B が ComfyUI 内で完結)。

外部スクリプト・中間 mp4 不要。依存は numpy + PIL + torch(ComfyUI 同梱)。
ロジックは projects/B03 の make_reference_video.py と同一。
"""
import math
import numpy as np
import torch
from PIL import Image, ImageDraw

# 基本カメラ動作(17種)。cam_state.apply() がこのトークンを解釈する。
BASE_MOTIONS = ["dolly_in", "dolly_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
                "truck_left", "truck_right", "pedestal_up", "pedestal_down",
                "roll_cw", "roll_ccw", "zoom_in", "zoom_out", "orbit_cw", "orbit_ccw", "static"]

# 複合カメラワークの別名(ドロップダウンに出る)。値は基本トークンの "+" 連結。
# ★ カメラワークを増やすときはここに1行足すだけ(例: "high_angle": "pedestal_up+tilt_down")
ALIASES = {
    "low_angle": "pedestal_down+tilt_up",   # カメラが下がって被写体を見上げる=ローアングル
}

# ドロップダウン表示順(別名 → 基本動作)
MOTIONS = list(ALIASES.keys()) + BASE_MOTIONS

SCENE_CENTER = np.array([0.0, 1.6, 7.0])


def expand_motion(motion):
    """別名を基本トークン列に展開('low_angle' → ['pedestal_down','tilt_up'])。複合 '+' も可。"""
    parts = []
    for tok in str(motion).split("+"):
        tok = tok.strip()
        if not tok:
            continue
        if tok in ALIASES:
            parts.extend(p.strip() for p in ALIASES[tok].split("+"))
        else:
            parts.append(tok)
    return parts


def _box(cx, cy, cz, sx, sy, sz, albedo):
    x0, x1 = cx - sx/2, cx + sx/2
    y0, y1 = cy - sy/2, cy + sy/2
    z0, z1 = cz - sz/2, cz + sz/2
    v = {
        "nx": [(x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0)],
        "px": [(x1,y0,z1),(x1,y0,z0),(x1,y1,z0),(x1,y1,z1)],
        "ny": [(x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1)],
        "py": [(x0,y1,z1),(x1,y1,z1),(x1,y1,z0),(x0,y1,z0)],
        "nz": [(x1,y0,z0),(x0,y0,z0),(x0,y1,z0),(x1,y1,z0)],
        "pz": [(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)],
    }
    return [(np.array(poly, dtype=float), albedo) for poly in v.values()]


def build_scene():
    faces = []
    for zi in range(-2, 16):
        for xi in range(-4, 5):
            shade = 0.55 if (zi + xi) % 2 == 0 else 0.38
            faces += [(np.array([(xi,0,zi),(xi+1,0,zi),(xi+1,0,zi+1),(xi,0,zi+1)], float), shade)]
    for zi in range(0, 15):
        shade = 0.30 if zi % 2 == 0 else 0.24
        faces += [(np.array([(-4,3.6,zi),(5,3.6,zi),(5,3.6,zi+1),(-4,3.6,zi+1)], float), shade)]
    for zi in range(1, 15, 2):
        for x in (-3.2, 3.2):
            faces += _box(x, 1.6, zi, 0.7, 3.2, 0.7, 0.62)
    for (bx, by, bz) in [(-1.4,1.0,5),(1.6,2.2,8),(0.0,0.7,11),(-1.8,2.6,12)]:
        faces += _box(bx, by, bz, 0.9, 0.9, 0.9, 0.7)
    faces += [(np.array([(-4,0,15),(5,0,15),(5,3.6,15),(-4,3.6,15)], float), 0.2)]
    return faces


def euler_R(yaw, pitch, roll):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    Ry = np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    Rx = np.array([[1,0,0],[0,cp,-sp],[0,sp,cp]])
    Rz = np.array([[cr,-sr,0],[sr,cr,0],[0,0,1]])
    return Ry @ Rx @ Rz


def ease(t):
    return t * t * (3 - 2 * t)


def cam_state(motion, t, amount):
    e = ease(t)
    C = np.array([0.0, 1.6, -2.0])
    yaw = pitch = roll = 0.0
    fmul = 1.0

    def apply(m):
        nonlocal C, yaw, pitch, roll, fmul
        A = amount
        if m == "pan_left":       yaw   += -math.radians(22*A) * e
        elif m == "pan_right":    yaw   +=  math.radians(22*A) * e
        elif m == "tilt_up":      pitch +=  math.radians(18*A) * e
        elif m == "tilt_down":    pitch += -math.radians(18*A) * e
        elif m == "roll_cw":      roll  +=  math.radians(20*A) * e
        elif m == "roll_ccw":     roll  += -math.radians(20*A) * e
        elif m == "dolly_in":     C[2]  +=  4.5*A * e
        elif m == "dolly_out":    C[2]  += -3.0*A * e
        elif m == "truck_left":   C[0]  += -2.6*A * e
        elif m == "truck_right":  C[0]  +=  2.6*A * e
        elif m == "pedestal_up":  C[1]  +=  1.8*A * e
        elif m == "pedestal_down":C[1]  += -1.2*A * e
        elif m == "zoom_in":      fmul  *= (1 + 0.9*A * e)
        elif m == "zoom_out":     fmul  *= 1.0 / (1 + 0.7*A * e)
        elif m == "static":       pass
        elif m in ("orbit_cw", "orbit_ccw"):
            sign = 1 if m == "orbit_cw" else -1
            ctr = SCENE_CENTER
            r0 = C - ctr
            radius = math.hypot(r0[0], r0[2])
            a0 = math.atan2(r0[0], r0[2])
            ang = a0 + sign * math.radians(45*A) * e
            C[0] = ctr[0] + radius*math.sin(ang)
            C[2] = ctr[2] + radius*math.cos(ang)
            d = ctr - C
            yaw = math.atan2(d[0], d[2])
            pitch = -math.atan2(d[1], math.hypot(d[0], d[2]))
        else:
            raise ValueError("unknown motion component: %r" % m)

    for comp in expand_motion(motion):
        apply(comp)
    return C, yaw, pitch, roll, fmul


def _clip_near(poly_cam, near=0.05):
    out = []
    n = len(poly_cam)
    for i in range(n):
        a = poly_cam[i]; b = poly_cam[(i+1) % n]
        ain = a[2] > near; bin_ = b[2] > near
        if ain:
            out.append(a)
        if ain != bin_:
            tt = (near - a[2]) / (b[2] - a[2])
            out.append(a + tt * (b - a))
    return np.array(out) if len(out) >= 3 else None


def _hfov_focal(W, hfov_deg):
    return (W/2.0) / math.tan(math.radians(hfov_deg)/2.0)


def render_frame(faces, C, yaw, pitch, roll, fmul, W, H, base_f):
    Rcw = euler_R(yaw, pitch, roll)
    Rwc = Rcw.T
    f = base_f * fmul
    cx, cy = W/2.0, H/2.0
    light = np.array([0.3, 0.8, -0.5]); light = light/np.linalg.norm(light)

    drawables = []
    for verts, albedo in faces:
        cam = (Rwc @ (verts - C).T).T
        cam = _clip_near(cam)
        if cam is None:
            continue
        zavg = cam[:, 2].mean()
        nrm = np.cross(cam[1]-cam[0], cam[2]-cam[0])
        ln = np.linalg.norm(nrm)
        shade = 1.0
        if ln > 1e-8:
            nrm = nrm/ln
            shade = 0.45 + 0.55*max(0.0, abs(float(nrm @ light)))
        xs = f * cam[:, 0] / cam[:, 2] + cx
        ys = cy - f * cam[:, 1] / cam[:, 2]
        pts = list(zip(xs.tolist(), ys.tolist()))
        val = int(np.clip(albedo*shade*255, 0, 255))
        drawables.append((zavg, pts, val))

    drawables.sort(key=lambda d: -d[0])
    img = Image.new("RGB", (W, H), (16, 18, 24))
    dr = ImageDraw.Draw(img)
    for _, pts, val in drawables:
        dr.polygon(pts, fill=(val, val, val), outline=(max(val-18, 0),)*3)
    return img


class B03CameraReferenceGenerator:
    """中立3Dシーンからカメラ動作の参照フレーム(IMAGEバッチ)を生成。LTXAddVideoICLoRAGuide.image へ。"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "motion": (MOTIONS, {"default": "orbit_cw"}),
                "frames": ("INT", {"default": 97, "min": 1, "max": 1000}),
                "width": ("INT", {"default": 544, "min": 64, "max": 4096, "step": 8}),
                "height": ("INT", {"default": 960, "min": 64, "max": 4096, "step": 8}),
                "amount": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05}),
                "hfov": ("FLOAT", {"default": 55.0, "min": 20.0, "max": 110.0, "step": 1.0}),
            },
            "optional": {
                # 複合や任意指定。空でないとき motion を上書き(例: "truck_left+pan_right")
                "custom_motion": ("STRING", {"default": "", "multiline": False}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("frames",)
    FUNCTION = "generate"
    CATEGORY = "B03/Camera"
    DESCRIPTION = "中立3Dシーンに正確なカメラ軌道を当てた参照フレーム列を生成(Mode B のIC-LoRAガイド入力)"

    def generate(self, motion, frames, width, height, amount, hfov, custom_motion=""):
        mot = custom_motion.strip() if custom_motion and custom_motion.strip() else motion
        faces = build_scene()
        base_f = _hfov_focal(width, hfov)
        arr = np.empty((frames, height, width, 3), dtype=np.float32)
        for i in range(frames):
            t = i/(frames-1) if frames > 1 else 0.0
            C, yaw, pitch, roll, fmul = cam_state(mot, t, amount)
            img = render_frame(faces, C, yaw, pitch, roll, fmul, width, height, base_f)
            arr[i] = np.asarray(img, dtype=np.float32) / 255.0
        return (torch.from_numpy(arr),)


NODE_CLASS_MAPPINGS = {"B03CameraReferenceGenerator": B03CameraReferenceGenerator}
NODE_DISPLAY_NAME_MAPPINGS = {"B03CameraReferenceGenerator": "🎥 Camera Reference (3D)"}
