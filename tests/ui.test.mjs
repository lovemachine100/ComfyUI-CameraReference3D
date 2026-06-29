// Headless UI test for web/preview.js — loads it in a mocked ComfyUI/litegraph env.
// Self-contained: builds a temp sandbox so preview.js's `import ../../scripts/app.js` resolves to a mock.
// Run:  node tests/ui.test.mjs
import { mkdirSync, writeFileSync, copyFileSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");
const sand = join(here, ".sandbox");
const pkgDir = join(sand, "extensions", "ComfyUI-CameraReference3D");
mkdirSync(join(sand, "scripts"), { recursive: true });
mkdirSync(pkgDir, { recursive: true });
writeFileSync(join(sand, "package.json"), '{"type":"module"}');
writeFileSync(join(sand, "scripts", "app.js"),
  "export const app = { registerExtension(d){ globalThis.__CR3D_EXT = d; } };\n");
copyFileSync(join(repo, "web", "preview.js"), join(pkgDir, "preview.js"));

let PASS = 0, FAIL = 0; const fails = [];
const ok = (n, c, x) => { if (c) { PASS++; console.log("  PASS " + n); } else { FAIL++; fails.push(n + (x ? " :: " + x : "")); console.log("  FAIL " + n + (x ? " :: " + x : "")); } };
const tick = () => new Promise((r) => setTimeout(r, 0));

function makeEl(tag) {
  const L = {};
  const el = { tagName: tag, style: {}, children: [],
    addEventListener: (t, cb) => { (L[t] = L[t] || []).push(cb); },
    appendChild: (c) => { el.children.push(c); return c; }, remove() {}, click() {},
    __fire: (t, ev) => { (L[t] || []).forEach((cb) => cb(ev || {})); } };
  if (tag === "video") { el.src = ""; el.play = () => Promise.resolve(); }
  if (tag === "div") { el.textContent = ""; }
  return el;
}
globalThis.document = { createElement: makeEl, body: { appendChild() {} } };
globalThis.window = { prompt: () => "x" };
globalThis.requestAnimationFrame = (cb) => { cb(); return 0; };
const META = {
  uploaded1: { width: 704, height: 1280, frames: 121, fps: 25 },
  myclip2: { width: 512, height: 512, frames: 60, fps: 30 },
  // parametric preview sample clips (previews/*.mp4 are 220×220)
  dolly_in: { width: 220, height: 220, frames: 49, fps: 24 },
  orbit_cw: { width: 220, height: 220, frames: 49, fps: 24 },
};
globalThis.fetch = async (url) => {
  const m = /name=([^&]+)/.exec(url); const name = m ? decodeURIComponent(m[1]) : "";
  const e = META[name];
  return e ? { ok: true, status: 200, json: async () => ({ ...e, name }) }
           : { ok: false, status: 404, json: async () => ({ error: "nf" }) };
};

const mkW = (name, value, type) => ({ name, value, type: type || "number", computeSize: () => [200, 20] });
function makeNode() {
  const node = { size: [240, 100], domWidgets: [], setSizeCalls: 0,
    widgets: [
      Object.assign(mkW("motion", "orbit_cw", "combo"), { options: { values: ["orbit_cw", "dolly_in", "uploaded1", "myclip2"] } }),
      mkW("frames", 97), mkW("width", 544), mkW("height", 960), mkW("amount", 1), mkW("hfov", 55), mkW("fps", 25),
      mkW("custom_motion", "", "text"), mkW("scene", "corridor", "combo"), mkW("props", "", "text"),
    ],
    addDOMWidget(n, t, el, o) { const w = { name: n, type: t, element: el, opts: o }; node.domWidgets.push(w); return w; },
    addWidget(type, name, value, cb) { const w = { type, name, value, callback: cb }; node.widgets.push(w); return w; },
    computeSize() { return [Math.max(this.size[0], 240), 220]; },
    setSize(s) { this.size = s; this.setSizeCalls++; }, setDirtyCanvas() {} };
  return node;
}
const W = (node, name) => node.widgets.find((w) => w.name === name);
async function select(node, value, viaCallback = false) {
  const mw = W(node, "motion"); mw.value = value;
  if (viaCallback && typeof mw.callback === "function") mw.callback(value);
  await tick(); await tick();
}

await import(new URL("file://" + join(pkgDir, "preview.js").replace(/\\/g, "/")));
const ext = globalThis.__CR3D_EXT;
if (!ext?.beforeRegisterNodeDef) { console.log("FATAL: extension not registered"); process.exit(1); }
function NodeType() {}
await ext.beforeRegisterNodeDef(NodeType, { name: "CameraReference3D" });
const create = async () => { const n = makeNode(); NodeType.prototype.onNodeCreated.call(n); await tick(); return n; };

console.log("T1 node creation / overflow guard (fixed-height box)");
{ const n = await create();
  ok("T1.video objectFit contain", n._b03?.video?.style.objectFit === "contain", n._b03?.video?.style.objectFit);
  ok("T1.preview box height 200px", n._b03?.box?.style.height === "200px", n._b03?.box?.style.height);
  ok("T1.preview box overflow hidden", n._b03?.box?.style.overflow === "hidden", n._b03?.box?.style.overflow);
  const pw = n.domWidgets.find((w) => w.type === "b03preview");
  ok("T1.preview widget fixed height 208", pw && typeof pw.computeSize === "function" && pw.computeSize()[1] === 208, pw && pw.computeSize && pw.computeSize()[1]);
  ok("T1.upload button", n.widgets.some((w) => w.type === "button")); }

console.log("T4 select uploaded video: width/height/frames/fps FIELDS become the clip's values");
{ const n = await create();
  await select(n, "uploaded1");
  ok("T4.width field -> 704", W(n, "width").value === 704, W(n, "width").value);
  ok("T4.height field -> 1280", W(n, "height").value === 1280, W(n, "height").value);
  ok("T4.frames field -> 121", W(n, "frames").value === 121, W(n, "frames").value);
  ok("T4.fps field -> 25", W(n, "fps").value === 25, W(n, "fps").value);
  ok("T4.label 704×1280", /704×1280/.test(n._b03.label.textContent), n._b03.label.textContent);
  ok("T4.video src uploaded1", /uploaded1/.test(n._b03.video.src), n._b03.video.src); }

console.log("T5 select via callback path also sets all fields");
{ const n = await create(); await select(n, "myclip2", true);
  ok("T5.width -> 512", W(n, "width").value === 512, W(n, "width").value);
  ok("T5.frames -> 60", W(n, "frames").value === 60, W(n, "frames").value);
  ok("T5.fps -> 30", W(n, "fps").value === 30, W(n, "fps").value); }

console.log("T6 parametric pan_right-like clip sets fields to PREVIEW clip values (220×220/49f/24fps)");
{ const n = await create(); await select(n, "uploaded1"); await select(n, "dolly_in");
  ok("T6.width field -> 220", W(n, "width").value === 220, W(n, "width").value);
  ok("T6.height field -> 220", W(n, "height").value === 220, W(n, "height").value);
  ok("T6.frames field -> 49", W(n, "frames").value === 49, W(n, "frames").value);
  ok("T6.fps field -> 24", W(n, "fps").value === 24, W(n, "fps").value);
  ok("T6.label 220×220", /220×220/.test(n._b03.label.textContent), n._b03.label.textContent); }

console.log("T7 all fields track the selected clip");
{ const n = await create();
  await select(n, "uploaded1"); ok("T7.uploaded1 704/121", W(n, "width").value === 704 && W(n, "frames").value === 121, W(n, "frames").value);
  await select(n, "dolly_in");  ok("T7.dolly_in 220/49", W(n, "width").value === 220 && W(n, "frames").value === 49, W(n, "frames").value);
  await select(n, "uploaded1"); ok("T7.back 704/121", W(n, "width").value === 704 && W(n, "frames").value === 121, W(n, "frames").value); }

console.log("T9 restore guard: changing motion while restoring does NOT overwrite fields");
{ const n = await create();
  const snap = ["width", "height", "frames", "fps"].map((k) => W(n, k).value);
  n.__b03restoring = true;
  W(n, "motion").value = "uploaded1"; // triggers value-setter with userChanged=false
  await tick(); await tick();
  ok("T9.fields preserved during restore", ["width", "height", "frames", "fps"].every((k, i) => W(n, k).value === snap[i]),
     JSON.stringify(["width", "height", "frames", "fps"].map((k) => W(n, k).value))); }

console.log("T8 overflow guard: fixed widget height + loadeddata refit");
{ const n = await create();
  const pw = n.domWidgets.find((w) => w.type === "b03preview");
  ok("T8.widget height fixed (208) regardless of video", pw.computeSize()[1] === 208, pw.computeSize()[1]);
  const b = n.setSizeCalls; n._b03.video.__fire("loadeddata"); await tick();
  ok("T8.setSize on loadeddata", n.setSizeCalls > b, b + "->" + n.setSizeCalls); }

rmSync(sand, { recursive: true, force: true });
console.log("\n==== UI: " + PASS + " passed, " + FAIL + " failed ====");
if (FAIL) { console.log("FAILURES:\n - " + fails.join("\n - ")); process.exit(1); }
