# -*- coding: utf-8 -*-
"""
ComfyUI-CameraReference3D

中立な 3D シーン(柱の回廊+床+天井)に、選んだカメラ動作を正確に当てて
「カメラ動作 参照フレーム列(IMAGE バッチ)」をその場で生成するノード。
出力を LTXAddVideoICLoRAGuide の image 入力に直結すると、Cameraman IC-LoRA が
そのカメラの動きだけを生成へ転写する(Mode B が ComfyUI 内で完結)。

パラメトリック生成は依存ゼロ(numpy + PIL + torch、ComfyUI 同梱)。
ロジックはスタンドアロン CLI make_reference_video.py と同一。

web/previews/ に動画 (.mp4/.webm/.mov/.gif) を置くと、その名前が motion ドロップダウンに
自動で並び「選択 → その動画自体を参照フレームとして使う」モードになる。動画モードでは
width / height / amount / hfov の widget は無視し、動画ネイティブの解像度を使う。尺は
frames で指定でき(動画全長から等間隔に frames 枚を抽出してカメラ軌道全体を保持。frames が
総フレーム数以上なら全フレーム)、生成 latent の length と揃えれば ICLoRAGuide のフレーム超過を
防げる。fps は動画ネイティブを優先。動画デコードのときだけ imageio/cv2 を遅延 import。
"""
import math
import os
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

# パラメトリックに生成できる名前(別名 → 基本動作)
MOTIONS = list(ALIASES.keys()) + BASE_MOTIONS

# web/previews/ に置いた動画も選択肢にする
PREVIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "previews")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".gif", ".mkv", ".avi")


def list_preview_stems():
    """web/previews/ 内の動画ファイル名(拡張子なし)を返す。"""
    try:
        return sorted({os.path.splitext(f)[0] for f in os.listdir(PREVIEW_DIR)
                       if f.lower().endswith(VIDEO_EXTS)})
    except OSError:
        return []


def motion_choices():
    """ドロップダウン候補 = パラメトリック名 + previews/ にしか無い動画名(末尾に追加)。"""
    stems = list_preview_stems()
    extra = [s for s in stems if s not in MOTIONS]
    return MOTIONS + extra


def is_parametric(name):
    """name が基本トークン(別名/複合含む)だけで構成されているか = その場で計算できるか。"""
    comps = expand_motion(name)
    return len(comps) > 0 and all(c in BASE_MOTIONS for c in comps)


def _find_preview_file(stem):
    """previews/ から stem に一致する動画ファイルの実パスを返す(無ければ None)。"""
    for ext in VIDEO_EXTS:
        p = os.path.join(PREVIEW_DIR, stem + ext)
        if os.path.isfile(p):
            return p
    return None


def load_video_frames(path):
    """動画をデコードし、ネイティブの全フレーム・解像度のまま返す。(tensor[N,H,W,3], native_fps)。"""
    native_fps = None
    raw = None
    # 1) imageio (imageio-ffmpeg 同梱) を優先
    try:
        import imageio.v2 as imageio
        rdr = imageio.get_reader(path)
        try:
            native_fps = float(rdr.get_meta_data().get("fps")) or None
        except Exception:
            native_fps = None
        raw = [f for f in rdr]
        rdr.close()
    except Exception:
        raw = None
    # 2) cv2 フォールバック
    if not raw:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        native_fps = float(fps) if fps and fps > 0 else None
        raw = []
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            raw.append(fr[:, :, ::-1])  # BGR -> RGB
        cap.release()
    if not raw:
        raise RuntimeError("動画をデコードできませんでした: %s" % path)

    # ネイティブ解像度・全フレームのまま(リサンプル/リサイズしない)
    frames_list = [np.asarray(Image.fromarray(np.asarray(fr)).convert("RGB"),
                              dtype=np.float32) / 255.0 for fr in raw]
    arr = np.stack(frames_list, axis=0)  # N, H, W, 3 (動画ネイティブ)
    return torch.from_numpy(arr), native_fps


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


def _ground(a=0.55, b=0.38, x0=-6, x1=7, z0=-3, z1=18):
    """市松模様の床グリッド。"""
    faces = []
    for zi in range(z0, z1):
        for xi in range(x0, x1):
            shade = a if (zi + xi) % 2 == 0 else b
            faces.append((np.array([(xi,0,zi),(xi+1,0,zi),(xi+1,0,zi+1),(xi,0,zi+1)], float), shade))
    return faces


def _sphere(cx, cy, cz, r, albedo=0.6, seg=10, rings=6):
    """ローポリ UV 球(quad 面)。"""
    faces = []
    for i in range(rings):
        th0, th1 = math.pi*i/rings, math.pi*(i+1)/rings
        for j in range(seg):
            ph0, ph1 = 2*math.pi*j/seg, 2*math.pi*(j+1)/seg
            def P(th, ph):
                return (cx + r*math.sin(th)*math.cos(ph), cy + r*math.cos(th), cz + r*math.sin(th)*math.sin(ph))
            faces.append((np.array([P(th0,ph0), P(th0,ph1), P(th1,ph1), P(th1,ph0)], float), albedo))
    return faces


def _subject(cx, cz, h, albedo=0.62):
    """人物プレースホルダ(脚+胴+頭の箱積み)。床(y=0)から高さ h。"""
    leg_h, torso_h, head_h = h*0.45, h*0.35, h*0.20
    faces = []
    faces += _box(cx, leg_h/2, cz, h*0.22, leg_h, h*0.18, albedo)
    faces += _box(cx, leg_h + torso_h/2, cz, h*0.34, torso_h, h*0.22, albedo)
    faces += _box(cx, leg_h + torso_h + head_h/2, cz, h*0.18, head_h, h*0.18, albedo)
    return faces


def parse_props(text):
    """props マルチライン DSL を面リストに変換。1行1プリミティブ、`#` はコメント。
    box cx cy cz sx sy sz [shade] / sphere cx cy cz r [shade] / pillar cx cz h [r] /
    wall z [h] / subject cx cz h / ground
    """
    faces = []
    if not text:
        return faces
    for ln, raw in enumerate(str(text).splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        kind = parts[0].lower()
        try:
            n = [float(x) for x in parts[1:]]
        except ValueError:
            raise ValueError("props 行 %d: 数値が不正: %r" % (ln, raw))
        if kind == "box" and len(n) >= 6:
            faces += _box(n[0], n[1], n[2], n[3], n[4], n[5], n[6] if len(n) > 6 else 0.6)
        elif kind == "sphere" and len(n) >= 4:
            faces += _sphere(n[0], n[1], n[2], n[3], n[4] if len(n) > 4 else 0.6)
        elif kind == "pillar" and len(n) >= 3:
            r = n[3] if len(n) > 3 else 0.5
            faces += _box(n[0], n[2]/2.0, n[1], r, n[2], r, 0.6)         # pillar cx cz h [r]
        elif kind == "wall" and len(n) >= 1:
            z, h = n[0], (n[1] if len(n) > 1 else 3.6)
            faces.append((np.array([(-5,0,z),(6,0,z),(6,h,z),(-5,h,z)], float), 0.30))
        elif kind == "subject" and len(n) >= 3:
            faces += _subject(n[0], n[1], n[2])
        elif kind == "ground":
            faces += _ground()
        else:
            raise ValueError("props 行 %d: 不明なタイプ/引数不足: %r" % (ln, raw))
    return faces


def build_scene(scene="corridor", props_text=""):
    """ベースシーン(corridor/ground/empty)+ props で組んだ面リストを返す。"""
    faces = []
    if scene == "corridor":
        faces += _ground(0.55, 0.38, -4, 5, -2, 16)
        for zi in range(0, 15):
            shade = 0.30 if zi % 2 == 0 else 0.24
            faces.append((np.array([(-4,3.6,zi),(5,3.6,zi),(5,3.6,zi+1),(-4,3.6,zi+1)], float), shade))
        for zi in range(1, 15, 2):
            for x in (-3.2, 3.2):
                faces += _box(x, 1.6, zi, 0.7, 3.2, 0.7, 0.62)
        for (bx, by, bz) in [(-1.4,1.0,5),(1.6,2.2,8),(0.0,0.7,11),(-1.8,2.6,12)]:
            faces += _box(bx, by, bz, 0.9, 0.9, 0.9, 0.7)
        faces.append((np.array([(-4,0,15),(5,0,15),(5,3.6,15),(-4,3.6,15)], float), 0.2))
    elif scene == "ground":
        faces += _ground()
    elif scene == "empty":
        pass
    faces += parse_props(props_text)
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


class CameraReference3D:
    """中立3Dシーンからカメラ動作の参照フレーム(IMAGEバッチ)を生成。LTXAddVideoICLoRAGuide.image へ。"""

    @classmethod
    def INPUT_TYPES(cls):
        # 候補は呼ばれるたび previews/ を走査して動的生成(動画を置けば再読込で並ぶ)
        return {
            "required": {
                "motion": (motion_choices(), {"default": "orbit_cw"}),
                # 生成する参照フレーム数。動画選択モードでも有効: 動画全長から等間隔に
                # この枚数だけ抽出する(カメラ軌道全体を保持したまま尺を揃えられる)。
                # 生成側 EmptyLTXVLatentVideo の length と一致させれば LTXAddVideoICLoRAGuide の
                # "Conditioning frames exceed the length of the latent sequence" を構造的に回避できる。
                "frames": ("INT", {"default": 97, "min": 1, "max": 1000,
                                   "tooltip": "参照フレーム数。動画選択時も有効(全長から等間隔抽出して尺を合わせる)。"
                                              "生成 latent の length と揃えると ICLoRAGuide のフレーム超過を防げる。"}),
                "width": ("INT", {"default": 544, "min": 64, "max": 4096, "step": 8}),
                "height": ("INT", {"default": 960, "min": 64, "max": 4096, "step": 8}),
                "amount": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05}),
                "hfov": ("FLOAT", {"default": 55.0, "min": 20.0, "max": 110.0, "step": 1.0}),
                # 出力 frames に対して後続ノードへ引き継ぐ fps。動画選択時はその実 fps が優先される
                "fps": ("FLOAT", {"default": 25.0, "min": 1.0, "max": 240.0, "step": 0.01}),
            },
            "optional": {
                # 複合や任意指定。空でないとき motion を上書き(例: "truck_left+pan_right")
                "custom_motion": ("STRING", {"default": "", "multiline": False}),
                # ベースシーン: corridor(柱の回廊) / ground(床のみ) / empty(何もなし)
                "scene": (["corridor", "ground", "empty"], {"default": "corridor"}),
                # 小道具 DSL(1行1プリミティブ)。box/sphere/pillar/wall/subject/ground を配置。
                # 例: "subject 0 7 1.7\nbox 2 0.5 6 1 1 1\nsphere -2 1 8 1.2"
                "props": ("STRING", {"default": "", "multiline": True}),
            },
        }

    RETURN_TYPES = ("IMAGE", "FLOAT")
    RETURN_NAMES = ("frames", "fps")
    FUNCTION = "generate"
    CATEGORY = "CameraReference3D"
    DESCRIPTION = "3Dシーン(corridor/ground/empty + 小道具 props)に正確なカメラ軌道を当てた参照フレーム列を生成。Cameraman=カメラ動作転写 / 3DREAL=構図ごと実写化 の grey blockout 入力に。previews/ の動画名を選べばその動画を参照に使う。fps を後続へ引き継ぐ"

    def generate(self, motion, frames, width, height, amount, hfov, fps=25.0,
                 custom_motion="", scene="corridor", props=""):
        mot = custom_motion.strip() if custom_motion and custom_motion.strip() else motion

        # パラメトリックでない名前(= previews/ に置いた動画)なら、その動画を参照として使う。
        # width / height / amount / hfov の widget は無視し、動画ネイティブの解像度をそのまま使う。
        # 尺は frames で指定: 動画全長から等間隔に frames 枚を抽出(カメラ軌道全体を保持)。
        # frames が動画の総フレーム数以上なら全フレームをそのまま使う(増やせないので clamp)。
        # fps は動画ネイティブを優先。
        if not is_parametric(mot):
            path = _find_preview_file(mot)
            if path is None:
                raise ValueError(
                    "motion %r はパラメトリック動作でも previews/ の動画でもありません。"
                    "基本トークンの組合せ(例 dolly_in+tilt_up)にするか web/previews/ に %s.mp4 を置いてください。"
                    % (mot, mot))
            tensor, native_fps = load_video_frames(path)
            # 防御的 coerce: 旧ワークフロー等で frames が ''/None で来ても全フレーム扱いにする。
            try:
                n_out = int(frames)
            except (TypeError, ValueError):
                n_out = 0
            n_native = int(tensor.shape[0])
            if n_out > 0 and n_out < n_native:
                idx = np.linspace(0, n_native - 1, n_out).round().astype(np.int64)
                tensor = tensor[idx]
            out_fps = float(native_fps) if native_fps else float(fps)
            return (tensor, out_fps)

        # パラメトリック生成(従来パス)
        faces = build_scene(scene, props)
        base_f = _hfov_focal(width, hfov)
        arr = np.empty((frames, height, width, 3), dtype=np.float32)
        for i in range(frames):
            t = i/(frames-1) if frames > 1 else 0.0
            C, yaw, pitch, roll, fmul = cam_state(mot, t, amount)
            img = render_frame(faces, C, yaw, pitch, roll, fmul, width, height, base_f)
            arr[i] = np.asarray(img, dtype=np.float32) / 255.0
        return (torch.from_numpy(arr), float(fps))


NODE_CLASS_MAPPINGS = {"CameraReference3D": CameraReference3D}
NODE_DISPLAY_NAME_MAPPINGS = {"CameraReference3D": "🎥 Camera Reference (3D)"}


# ---- アップロード用サーバルート: 動画を web/previews/ に保存(ComfyUI 実行時のみ登録) ----
def _register_upload_route():
    try:
        from server import PromptServer
        from aiohttp import web
    except Exception:
        return  # importlib 単体テスト等、ComfyUI サーバが無い環境では何もしない
    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        return

    import re
    _SAFE = re.compile(r"[^A-Za-z0-9._-]+")

    def _sanitize_stem(raw):
        # ユーザが拡張子付きで入れても落とす
        if raw and raw.lower().endswith(VIDEO_EXTS):
            raw = os.path.splitext(raw)[0]
        stem = _SAFE.sub("_", raw or "").strip("._")
        return stem or "clip"

    def _probe_video_meta(path):
        """動画の (width, height, frames, fps) を全デコードせず軽量に取得。"""
        w = h = frames = 0
        fps = 0.0
        # 1) imageio (imageio-ffmpeg 同梱) のメタデータを優先
        try:
            import imageio.v2 as imageio
            rdr = imageio.get_reader(path)
            meta = rdr.get_meta_data() or {}
            fps = float(meta.get("fps") or 0.0)
            size = meta.get("size")
            if size and len(size) == 2:
                w, h = int(size[0]), int(size[1])
            nframes = meta.get("nframes")
            if isinstance(nframes, (int, float)) and nframes not in (0, float("inf")):
                frames = int(nframes)
            else:
                dur = meta.get("duration")
                if dur and fps:
                    frames = int(round(float(dur) * fps))
                else:
                    try:
                        frames = int(rdr.count_frames())
                    except Exception:
                        frames = 0
            rdr.close()
        except Exception:
            pass
        # 2) 取りこぼし(解像度/枚数)は cv2 のコンテナメタで補完(瞬時、デコード無し)
        if (w == 0 or h == 0 or frames == 0):
            try:
                import cv2
                cap = cv2.VideoCapture(path)
                if w == 0:
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                if h == 0:
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if frames == 0:
                    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if not fps:
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                cap.release()
            except Exception:
                pass
        return {"width": int(w), "height": int(h), "frames": int(frames), "fps": float(fps)}

    @instance.routes.get("/camera_reference_3d/meta")
    async def _meta(request):
        """previews/ の動画の解像度・フレーム数・fps を返す(ノード UI の自動反映用)。"""
        stem = (request.query.get("name") or "").strip()
        if not stem:
            return web.json_response({"error": "name がありません"}, status=400)
        path = _find_preview_file(stem)
        if path is None:
            return web.json_response({"error": "previews に動画がありません: %s" % stem}, status=404)
        try:
            meta = _probe_video_meta(path)
        except Exception as e:
            return web.json_response({"error": "メタ取得失敗: %s" % e}, status=500)
        meta["name"] = stem
        return web.json_response(meta)

    @instance.routes.post("/camera_reference_3d/upload")
    async def _upload(request):
        try:
            reader = await request.multipart()
        except Exception:
            return web.json_response({"error": "multipart ではありません"}, status=400)

        desired = ""
        orig_name = ""
        data = b""
        async for part in reader:
            if part.name == "name":
                desired = (await part.text()).strip()
            elif part.name in ("file", "video", "image"):
                orig_name = part.filename or ""
                buf = bytearray()
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    buf.extend(chunk)
                data = bytes(buf)

        if not data:
            return web.json_response({"error": "ファイル本体がありません"}, status=400)
        ext = os.path.splitext(orig_name)[1].lower()
        if ext not in VIDEO_EXTS:
            return web.json_response(
                {"error": "対応していない拡張子: %r (許可: %s)" % (ext, ", ".join(VIDEO_EXTS))},
                status=400)

        stem = _sanitize_stem(desired or os.path.basename(orig_name))
        os.makedirs(PREVIEW_DIR, exist_ok=True)
        # 衝突したら _1, _2, ... で必ず別名にする(上書きしない)
        target = os.path.join(PREVIEW_DIR, stem + ext)
        renamed = False
        i = 1
        while os.path.exists(target):
            renamed = True
            target = os.path.join(PREVIEW_DIR, "%s_%d%s" % (stem, i, ext))
            i += 1

        # PREVIEW_DIR の外に出ないことを最終確認(多重防御)
        if os.path.commonpath([os.path.abspath(target), os.path.abspath(PREVIEW_DIR)]) != os.path.abspath(PREVIEW_DIR):
            return web.json_response({"error": "不正な保存先"}, status=400)

        with open(target, "wb") as f:
            f.write(data)

        final = os.path.basename(target)
        return web.json_response({
            "name": os.path.splitext(final)[0],   # ドロップダウンに足す stem
            "filename": final,
            "renamed": renamed,
        })


_register_upload_route()
