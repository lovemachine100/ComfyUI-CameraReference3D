# ComfyUI-CameraReference3D 🎥

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

**`🎥 Camera Reference (3D)`** (`CameraReference3D`, category `CameraReference3D`)

| Input | Description |
|---|---|
| `motion` | Camera-motion dropdown — 17 base moves + alias `low_angle`, **plus any video dropped into `web/previews/`** (see below) |
| `frames` | Frame count — match your generation's `length` (typ. 97 / 49) |
| `width` / `height` | Resolution — match the generation (typ. 544×960) |
| `amount` | Motion strength multiplier (0–3, default 1.0) |
| `hfov` | Horizontal field of view in degrees (default 55°) |
| `fps` | Frames-per-second to carry to downstream nodes (default 25). **Parametric moves only** — a dropped video uses its own native fps. |
| `custom_motion` *(optional)* | Override `motion` with text. Combine base tokens with `+` (e.g. `dolly_in+tilt_up`). Empty → use `motion`. |
| `scene` *(optional)* | Base scene: `corridor` (the pillared hall, default), `ground` (checker floor only), or `empty` (nothing). |
| `props` *(optional)* | Multiline prop DSL — compose your own grey blockout on top of the base scene (see below). |

Outputs:
- `frames` — `IMAGE` (`N×H×W×3`) → `LTXAddVideoICLoRAGuide.image`
- `fps` — `FLOAT` → wire into downstream video nodes (save / VHS / conditioning) so timing is inherited

When you pick a `motion`, a sample clip of that move previews live on the node.

### Compose a grey blockout scene (`scene` + `props`)

Beyond the default corridor, pick a `scene` base and place primitives via the `props` text box
(one per line, `#` starts a comment) to build your own grey 3D blockout — ideal as the input to a
**render-to-real IC-LoRA** (e.g. fal's 3DREAL), which keeps the composition/layout and photorealizes it.

```
# <type> <args...>
ground                      # checker floor grid
subject  -1.2 7 1.7         # humanoid placeholder: cx cz height  (legs+torso+head)
box       0 0.6 6  1.4 1.2 1.4   # cx cy cz  sx sy sz
sphere   -2.5 1.2 10 1.2     # cx cy cz  radius
pillar    3 11 3.2 0.4       # cx cz height [radius]
wall      14                 # back wall at depth z [height]
```

Coordinates: `+x` right, `+y` up, `+z` into the scene (camera starts near `z=-2` looking toward `+z`).
The composed scene is driven by the same `motion`/`amount`/`hfov` camera system. In video mode these
parametric inputs (incl. `scene`/`props`) are hidden.

> Validated with **3DREAL**: a `subject` blockout photorealizes into a real person (clothing, face),
> faithfully following the blockout outline and camera move. Inorganic props (box/sphere/pillar/wall)
> stay grey — 3DREAL specializes in subject realification, not arbitrary material assignment.

### Use your own clip as the reference (drop-in videos)

Drop any video (`.mp4` / `.webm` / `.mov` / `.gif` / …) into `web/previews/` and **its name appears
in the `motion` dropdown** after a page refresh. Selecting it makes the node **decode that video and output
its native frames, resolution and fps unchanged** as the reference batch — instead of the parametric
corridor render.

In this video mode the geometry widgets don't apply, so the node UI **hides `amount`, `hfov`, `frames`,
`width`, `height` and `fps`** while a video is selected (the clip itself dictates frame count, resolution
and fps). Pick a parametric move again and they reappear. Names that match a parametric move (or a
`+`-composite of base tokens) still render parametrically and honor those widgets. Video decoding lazily
imports `imageio` / `cv2` only when a clip is actually used — the parametric path stays dependency-free.

#### Upload a clip from the node (no manual file copying)

The node has a **`📤 動画をアップロード → previews`** button. Click it, pick a local video, and (optionally)
edit the save name — the file is POSTed to a server route (`/camera_reference_3d/upload`) that writes it
into `web/previews/` and the name is added to the dropdown immediately (no page reload). **Name collisions
never overwrite**: an existing `name.mp4` makes the upload land as `name_1.mp4`, `name_2.mp4`, … and the
final name is reported back. The route validates the extension against the allowed video types and
sanitizes the name (no path traversal). The thumbnail preview expects `.mp4`; other formats still work as
references but won't show an on-node preview clip.

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
(`make_reference_video.py`) is kept alongside the node for rendering these clips.

`nodes.py` / `preview.js` changes take effect on **ComfyUI restart**.

## License

MIT (this node). Note the camera-motion transfer is designed for the LTX-2.3 **Cameraman
IC-LoRA**, whose weights carry their own license (the v2 LoRA is research / personal use,
non-commercial) — that is separate from this node's MIT terms.
