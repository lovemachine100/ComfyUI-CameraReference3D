# Tests

Two suites cover the node:

| Suite | What it checks | Run |
|---|---|---|
| `ui.test.mjs` | `web/preview.js` UI logic in a mocked ComfyUI/litegraph env: overflow guard (video max-height 240px), preview/label update on motion change via **both** the widget-callback path **and** the value-setter path, that selecting a clip **never** writes `frames`/`width`/`height`, that the label reflects the current state in video & parametric modes, and that `loadedmetadata` recomputes node size. | `node tests/ui.test.mjs` |
| `backend.test.py` | `nodes.py generate()`: video mode resamples the clip to `frames` (uniform sampling), clamps when `frames` ≥ native, treats `frames=0`/`""` as all-native, preserves native resolution; parametric mode honors `frames`/`width`/`height`. Optionally hits the live `/camera_reference_3d/meta` route (auto-skipped if ComfyUI isn't running). | `python tests/backend.test.py` |

The UI suite is self-contained (it writes a temp `.sandbox/` so `preview.js`'s `import ../../scripts/app.js` resolves to a mock, then cleans up). The backend suite copies one bundled preview clip to a temp non-parametric name to exercise the video branch.

Expected: **UI 21 passed / Backend 12 passed** (Backend shows 8 if `/meta` is skipped because ComfyUI isn't running).
