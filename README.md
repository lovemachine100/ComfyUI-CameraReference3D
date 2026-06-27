# ComfyUI-B03-CameraReference 🎥

**Camera Reference (3D)** — a ComfyUI custom node that generates an exact, parametric
*camera-motion reference frame sequence* by flying a chosen camera trajectory through a
neutral 3D scene (a pillared corridor with floor and ceiling). Wire the output straight
into `LTXAddVideoICLoRAGuide.image` and the LTX-2.3 **Cameraman IC-LoRA** transfers *only
the camera movement* onto your generation — no external 3D app, no intermediate mp4.

The IC-LoRA reads only the **motion parallax** of the reference, so a neutral grey scene
is ideal (a subject-free reference transfers more cleanly).

```
[🎥 Camera Reference (3D)]  pick a motion
        │  IMAGE batch (neutral grey 3D camera move), generated in-node
        ▼
[LTXAddVideoICLoRAGuide.image] + Cameraman v2 LoRA + start image + prompt
        ▼
the start image keeps its content; only the camera movement is transferred
```

## Install

ComfyUI Manager (once published) or manual:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lovemachine100/ComfyUI-CameraReference3D
# restart ComfyUI
```

Dependencies (`numpy`, `Pillow`, `torch`) all ship with ComfyUI — nothing extra to install.

## Node

**`🎥 Camera Reference (3D)`** (`B03CameraReferenceGenerator`, category `B03/Camera`)

| Input | Description |
|---|---|
| `motion` | Camera-motion dropdown — 17 base moves + alias `low_angle`, **plus any video dropped into `web/previews/`** (see below) |
| `frames` | Frame count — match your generation's `length` (typ. 97 / 49) |
| `width` / `height` | Resolution — match the generation (typ. 544×960) |
| `amount` | Motion strength multiplier (0–3, default 1.0) |
| `hfov` | Horizontal field of view in degrees (default 55°) |
| `fps` | Frames-per-second to carry to downstream nodes (default 25; for a dropped video its native fps is used instead) |
| `custom_motion` *(optional)* | Override `motion` with text. Combine base tokens with `+` (e.g. `dolly_in+tilt_up`). Empty → use `motion`. |

Outputs:
- `frames` — `IMAGE` (`N×H×W×3`) → `LTXAddVideoICLoRAGuide.image`
- `fps` — `FLOAT` → wire into downstream video nodes (save / VHS / conditioning) so timing is inherited

When you pick a `motion`, a sample clip of that move previews live on the node.

### Use your own clip as the reference (drop-in videos)

Drop any video (`.mp4` / `.webm` / `.mov` / `.gif` / …) into `web/previews/` and **its name appears
in the `motion` dropdown** after a page refresh. Selecting it makes the node **decode that video** and
resample it to `frames` × `width` × `height` as the reference batch (its native fps flows out of `fps`),
instead of the parametric corridor render. Names that match a parametric move (or a `+`-composite of base
tokens) still render parametrically. Video decoding lazily imports `imageio` / `cv2` only when a clip is
actually used — the parametric path stays dependency-free.

### Base motions (17)

`pan_left/right` · `tilt_up/down` · `dolly_in/out` (translate fwd/back) ·
`truck_left/right` (translate sideways) · `pedestal_up/down` (translate vertically) ·
`roll_cw/ccw` · `zoom_in/out` (focal length) · `orbit_cw/ccw` · `static`

> Dolly (camera translation) and zoom (focal length) are kept as distinct moves.

### Aliases (compound camera work)

- `low_angle` = `pedestal_down+tilt_up` (camera lowers and looks up)

## Extending the moves

**Add a compound move (1 line):** append to `ALIASES` in `nodes.py` — it appears in the
dropdown automatically (`MOTIONS = list(ALIASES) + BASE_MOTIONS`):

```python
ALIASES = {
    "low_angle":    "pedestal_down+tilt_up",
    "high_angle":   "pedestal_up+tilt_down",   # add: look down
    "crane_reveal": "pedestal_up+dolly_out",   # add: crane pull-back
}
```

**Add a brand-new base move:** add a branch in `cam_state().apply()` and append the token
to `BASE_MOTIONS`. Camera state `C` (x,y,z) / `yaw,pitch,roll` / `fmul` (focal multiplier)
is driven by `e` (eased 0→1 progress) and `A` (amount):

```python
elif m == "my_move":  C[0] += 2.0*A*e   # e.g. translate right
```

**Add its on-node preview clip** (220×220, lives in `web/previews/<name>.mp4`) and add
`<name>` to the `KNOWN` set in `web/preview.js`. A standalone CLI with identical logic
(`make_reference_video.py`) is kept in the upstream B03 project for rendering these clips.

`nodes.py` / `preview.js` changes take effect on **ComfyUI restart**.

## License

MIT (this node). Note the camera-motion transfer is designed for the LTX-2.3 **Cameraman
IC-LoRA**, whose weights carry their own license (the v2 LoRA is research / personal use,
non-commercial) — that is separate from this node's MIT terms.
