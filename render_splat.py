"""
Splat camera nodes (ComfyUI-CameraReference3D) — render a static Gaussian splat along nicer camera trajectories
than core RenderSplat's forced full 360 turntable.

- RenderSplatPath:      simple bounded front arc / dolly-in (kept for backward compat).
- RenderSplatCinematic: cinematic presets (orbit_push, pan_reveal, crane_up, dolly_zoom,
                        handheld, float_bob) with easing + global bob/handheld overlays.

Both reuse the core rasterizer internals so image quality matches RenderSplat exactly;
only the per-frame camera differs. A splat is a frozen 3D snapshot, so these move the
CAMERA (the subject itself cannot be animated).

Framing is HEIGHT-based (subject fills `subject_fill` of the frame height regardless of
aspect), so tall / 9:16 frames don't leave the subject small and far.
"""

import math
import random
import torch
import comfy.model_management
import comfy.utils

try:
    from comfy_extras.nodes_gaussian_splat import (
        _gaussian_item,
        _render_gaussian,
        _orbit_camera_info,
        _quantile,
        _hex_to_rgb,
    )
    _SPLAT_AVAILABLE = True
    _SPLAT_IMPORT_ERR = None
except Exception as _e:  # ComfyUI without the gaussian-splat internals -> disable these nodes gracefully
    _SPLAT_AVAILABLE = False
    _SPLAT_IMPORT_ERR = _e


def _frame_geo(splat, i, device, opacity_threshold, fov, width, height, subject_fill):
    xyz, rgb, opacity, scale, rot = _gaussian_item(splat, i, device)
    if opacity_threshold > 0:
        keep = opacity >= opacity_threshold
        xyz, rgb, opacity, scale, rot = xyz[keep], rgb[keep], opacity[keep], scale[keep], rot[keep]
    if xyz.shape[0] > 8:
        # ROBUST framing: a percentile bounding box ignores stray-gaussian smears (hallucinated
        # ponytail/back) that otherwise inflate the bounds and shove the subject small + off-center.
        xf = xyz.float()
        lo = torch.quantile(xf, 0.03, dim=0)
        hi = torch.quantile(xf, 0.97, dim=0)
        center = (lo + hi) / 2.0                      # look-at = box center -> subject centered
        extent = max(float((hi - lo).max().item()) / 2.0, 1e-4)  # half the subject's largest (upright) dimension
    else:
        center = xyz.mean(0) if xyz.shape[0] else torch.zeros(3, device=device)
        extent = 1.0
    # Core rasterizer's focal length uses min(width, height): projected_size = 2*extent*f/d,
    # f = (min/2)/tan(fov/2). Solve d so the subject fills `subject_fill` of the HEIGHT,
    # independent of aspect (fixes "subject too far" on tall / 9:16 frames).
    m = min(int(width), int(height))
    sf = max(float(subject_fill), 1e-3)
    dist0 = float(extent * m / (math.tan(math.radians(fov) / 2) * sf * int(height)))
    return xyz, rgb, opacity, scale, rot, center, dist0, extent


def _ease(u, mode):
    if mode == "ease_in_out":
        return 0.5 - 0.5 * math.cos(math.pi * u)
    if mode == "ease_out":
        return math.sin(math.pi / 2 * u)
    if mode == "ease_in":
        return 1.0 - math.cos(math.pi / 2 * u)
    return u  # linear


class RenderSplatPath:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "splat": ("SPLAT",),
                "width": ("INT", {"default": 352, "min": 64, "max": 2048, "step": 8}),
                "height": ("INT", {"default": 512, "min": 64, "max": 2048, "step": 8}),
                "frames": ("INT", {"default": 49, "min": 1, "max": 240}),
                "mode": (["front_arc", "dolly_in", "arc_and_dolly"], {"default": "front_arc"}),
                "sweep": (["pingpong", "linear"], {"default": "pingpong"}),
                "yaw_range": ("FLOAT", {"default": 32.0, "min": 0.0, "max": 340.0, "step": 1.0}),
                "yaw_center": ("FLOAT", {"default": 66.0, "min": -180.0, "max": 180.0, "step": 1.0}),
                "pitch": ("FLOAT", {"default": 6.0, "min": -89.0, "max": 89.0, "step": 1.0}),
                "dolly_amount": ("FLOAT", {"default": 0.30, "min": 0.0, "max": 0.85, "step": 0.01}),
                "fov": ("FLOAT", {"default": 35.0, "min": 1.0, "max": 120.0, "step": 1.0}),
                "subject_fill": ("FLOAT", {"default": 0.72, "min": 0.3, "max": 1.5, "step": 0.02,
                                 "tooltip": "Fraction of the frame HEIGHT the subject fills (aspect-independent). Higher = closer/bigger."}),
                "splat_scale": ("FLOAT", {"default": 1.0, "min": 0.1, "max": 5.0, "step": 0.05}),
                "sharpen": ("FLOAT", {"default": 3.0, "min": 1.0, "max": 8.0, "step": 0.5}),
                "headlight_shading": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 3.0, "step": 0.05}),
                "opacity_threshold": ("FLOAT", {"default": 0.40, "min": 0.0, "max": 1.0, "step": 0.01}),
                "render_style": (["color", "clay", "depth", "normal"], {"default": "color"}),
                "background": ("STRING", {"default": "#848484"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "render"
    CATEGORY = "3d/splat"
    DESCRIPTION = "Render a splat along a bounded front arc / dolly instead of a forced full 360."

    def render(self, splat, width, height, frames, mode, sweep, yaw_range, yaw_center, pitch,
               dolly_amount, fov, subject_fill, splat_scale, sharpen, headlight_shading,
               opacity_threshold, render_style, background):
        bg = _hex_to_rgb(background)
        n = max(1, int(frames))
        device = comfy.model_management.get_torch_device()
        do_arc = mode in ("front_arc", "arc_and_dolly")
        do_dolly = mode in ("dolly_in", "arc_and_dolly")
        imgs, masks = [], []
        pbar = comfy.utils.ProgressBar(splat.positions.shape[0] * n) if splat.positions.shape[0] * n > 1 else None
        for i in range(splat.positions.shape[0]):
            xyz, rgb, opacity, scale, rot, center, dist0, _extent = _frame_geo(
                splat, i, device, opacity_threshold, fov, width, height, subject_fill)
            for fr in range(n):
                u = fr / (n - 1) if n > 1 else 0.0
                if sweep == "pingpong":
                    yaw_phase = math.sin(2 * math.pi * u)
                    dolly_phase = (1.0 - math.cos(2 * math.pi * u)) / 2.0
                else:
                    yaw_phase, dolly_phase = 2.0 * u - 1.0, u
                yaw = yaw_center + (yaw_range / 2.0) * yaw_phase if do_arc else yaw_center
                dist = dist0 * (1.0 - dolly_amount * dolly_phase) if do_dolly else dist0
                cam = _orbit_camera_info(yaw, pitch, dist, fov, center, device)
                img, mask = _render_gaussian(xyz, rgb, opacity, scale, rot, width, height, splat_scale,
                                             bg, cam, sharpen=sharpen, headlight_shading=headlight_shading,
                                             render_style=render_style)
                imgs.append(img); masks.append(mask)
                if pbar is not None: pbar.update(1)
        return (torch.stack(imgs), torch.stack(masks))


class RenderSplatCinematic:
    PRESETS = ["orbit_push", "pan_reveal", "crane_up", "dolly_zoom", "handheld", "float_bob",
               "push_in", "pull_back", "truck", "boom_down", "spiral", "sway"]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "splat": ("SPLAT",),
                "width": ("INT", {"default": 352, "min": 64, "max": 2048, "step": 8}),
                "height": ("INT", {"default": 512, "min": 64, "max": 2048, "step": 8}),
                "frames": ("INT", {"default": 49, "min": 1, "max": 240}),
                "preset": (cls.PRESETS, {"default": "orbit_push",
                           "tooltip": "orbit_push=arc+push(hero) / pan_reveal=slow pan / crane_up=rise / "
                                      "dolly_zoom=vertigo / handheld=organic drift / float_bob=idle bob"}),
                "yaw_center": ("FLOAT", {"default": 66.0, "min": -180.0, "max": 180.0, "step": 1.0,
                               "tooltip": "Aim of the shot. ~66 = straight front of a TripoSplat portrait (matches a front-facing input image)."}),
                "pitch": ("FLOAT", {"default": 6.0, "min": -89.0, "max": 89.0, "step": 1.0}),
                "fov": ("FLOAT", {"default": 35.0, "min": 1.0, "max": 120.0, "step": 1.0}),
                "subject_fill": ("FLOAT", {"default": 0.72, "min": 0.3, "max": 1.5, "step": 0.02,
                                 "tooltip": "Fraction of the frame HEIGHT the subject fills (aspect-independent). Higher = closer/bigger. Raise this if the subject looks too far on 9:16."}),
                "move_amount": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05,
                                "tooltip": "Strength of the preset's primary move. Low (~0.3) keeps orientation close to the input; raise for bigger camera moves."}),
                "bob_amount": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                               "tooltip": "Global vertical bob overlaid on any preset (adds life)."}),
                "handheld_amount": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                                    "tooltip": "Global organic handheld jitter overlaid on any preset."}),
                "easing": (["ease_in_out", "ease_out", "ease_in", "linear"], {"default": "ease_in_out"}),
                "loop": ("BOOLEAN", {"default": True,
                         "tooltip": "Seamless loop: the move goes out and returns so first frame == last."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffff}),
                "splat_scale": ("FLOAT", {"default": 1.0, "min": 0.1, "max": 5.0, "step": 0.05}),
                "sharpen": ("FLOAT", {"default": 3.0, "min": 1.0, "max": 8.0, "step": 0.5}),
                "opacity_threshold": ("FLOAT", {"default": 0.40, "min": 0.0, "max": 1.0, "step": 0.01}),
                "render_style": (["color", "clay", "depth", "normal"], {"default": "color"}),
                "background": ("STRING", {"default": "#848484"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "render"
    CATEGORY = "3d/splat"
    DESCRIPTION = ("Cinematic camera presets for a static gaussian splat (orbit_push, pan_reveal, crane_up, "
                   "dolly_zoom, handheld, float_bob) with easing + optional bob/handheld overlay. "
                   "Height-based framing keeps the subject the right size on any aspect (incl. 9:16).")

    def render(self, splat, width, height, frames, preset, yaw_center, pitch, fov, subject_fill,
               move_amount, bob_amount, handheld_amount, easing, loop, seed, splat_scale, sharpen,
               opacity_threshold, render_style, background):
        bg = _hex_to_rgb(background)
        n = max(1, int(frames))
        device = comfy.model_management.get_torch_device()

        rng = random.Random(int(seed) or 12345)
        def comps(k=3):
            return [(rng.uniform(0.6, 2.2), rng.uniform(0, 2 * math.pi)) for _ in range(k)]
        hy, hp, hd = comps(), comps(), comps()
        def wander(tbl, u):
            return sum(math.sin(2 * math.pi * f * u + ph) for f, ph in tbl) / len(tbl)

        imgs, masks = [], []
        pbar = comfy.utils.ProgressBar(splat.positions.shape[0] * n) if splat.positions.shape[0] * n > 1 else None
        for i in range(splat.positions.shape[0]):
            xyz, rgb, opacity, scale, rot, center, dist0, extent = _frame_geo(
                splat, i, device, opacity_threshold, fov, width, height, subject_fill)
            for fr in range(n):
                u = fr / (n - 1) if n > 1 else 0.0
                if loop:
                    prog = (1.0 - math.cos(2 * math.pi * u)) / 2.0
                    sym = math.sin(2 * math.pi * u)
                else:
                    prog = _ease(u, easing)
                    sym = 2.0 * prog - 1.0

                yaw, pit, dist, fov_f = yaw_center, pitch, dist0, fov
                ox = oy = 0.0  # world-space look-at offset for translational moves (truck / boom / sway)
                if preset == "orbit_push":
                    yaw = yaw_center + (70.0 * move_amount / 2.0) * sym
                    dist = dist0 * (1.0 - 0.40 * move_amount * prog)
                elif preset == "pan_reveal":
                    arc = 90.0 * move_amount
                    yaw = yaw_center - arc / 2.0 + arc * prog
                elif preset == "crane_up":
                    yaw = yaw_center + (10.0 * move_amount) * sym
                    pit = pitch + (35.0 * move_amount) * prog
                    dist = dist0 * (1.0 - 0.15 * prog)
                elif preset == "dolly_zoom":
                    dist = dist0 * (1.0 - 0.40 * move_amount * prog)
                    fov_f = fov * (1.0 + 0.70 * move_amount * prog)
                elif preset == "handheld":
                    yaw = yaw_center + (4.0 * move_amount) * sym
                    pit = pitch + (2.0 * move_amount) * math.sin(2 * math.pi * u + 1.3)
                elif preset == "float_bob":
                    yaw = yaw_center + (5.0 * move_amount) * math.sin(2 * math.pi * u)
                    pit = pitch + (8.0 * move_amount) * math.sin(2 * math.pi * u)
                elif preset == "push_in":                       # straight dolly-in (no rotation)
                    dist = dist0 * (1.0 - 0.45 * move_amount * prog)
                elif preset == "pull_back":                     # dolly-out reveal
                    dist = dist0 * (1.0 + 0.70 * move_amount * prog)
                elif preset == "truck":                         # lateral camera slide (parallax)
                    ox = 0.70 * move_amount * extent * sym
                elif preset == "boom_down":                     # descend + slight look-down
                    oy = -0.55 * move_amount * extent * prog
                    pit = pitch - (8.0 * move_amount) * prog
                elif preset == "spiral":                        # orbit arc + rise + slight push (helix)
                    yaw = yaw_center + (45.0 * move_amount / 2.0) * sym
                    pit = pitch + (25.0 * move_amount) * prog
                    dist = dist0 * (1.0 - 0.10 * move_amount * prog)
                elif preset == "sway":                          # gentle organic side-to-side
                    yaw = yaw_center + (6.0 * move_amount) * math.sin(2 * math.pi * u)
                    ox = 0.22 * move_amount * extent * math.sin(2 * math.pi * u + 0.8)
                    pit = pitch + (3.0 * move_amount) * math.sin(2 * math.pi * u * 0.5)

                if bob_amount > 0:
                    pit += bob_amount * 6.0 * math.sin(2 * math.pi * u * 2.0)
                h_amt = handheld_amount + (0.5 if preset == "handheld" else 0.0)
                if h_amt > 0:
                    yaw += wander(hy, u) * 3.0 * h_amt
                    pit += wander(hp, u) * 2.0 * h_amt
                    dist *= (1.0 + wander(hd, u) * 0.05 * h_amt)

                pit = max(-89.0, min(89.0, pit))
                pivot = center if (ox == 0.0 and oy == 0.0) else center + torch.tensor(
                    [ox, oy, 0.0], device=device, dtype=center.dtype)
                cam = _orbit_camera_info(yaw, pit, dist, fov_f, pivot, device)
                img, mask = _render_gaussian(xyz, rgb, opacity, scale, rot, width, height, splat_scale,
                                             bg, cam, sharpen=sharpen, headlight_shading=0.0,
                                             render_style=render_style)
                imgs.append(img); masks.append(mask)
                if pbar is not None: pbar.update(1)
        return (torch.stack(imgs), torch.stack(masks))


if _SPLAT_AVAILABLE:
    NODE_CLASS_MAPPINGS = {
        "RenderSplatPath": RenderSplatPath,
        "RenderSplatCinematic": RenderSplatCinematic,
    }
    NODE_DISPLAY_NAME_MAPPINGS = {
        "RenderSplatPath": "Render Splat Path (bounded camera)",
        "RenderSplatCinematic": "Render Splat Cinematic (presets)",
    }
else:
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
    print("[ComfyUI-CameraReference3D] Render Splat nodes disabled: need a ComfyUI build with "
          "comfy_extras.nodes_gaussian_splat (0.26+). (%s)" % _SPLAT_IMPORT_ERR)
