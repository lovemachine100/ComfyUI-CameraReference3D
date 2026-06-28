import { app } from "../../scripts/app.js";

// Camera Reference (3D): motion を選ぶとノード上にその動きのサンプル映像をプレビュー表示
const PKG = "ComfyUI-CameraReference3D";
const BASE = `/extensions/${PKG}/previews/`;
// 固定リストは持たない: 選択名の <name>.mp4 を素直に読み、無ければ static.mp4 にフォールバック。
// これで previews/ に動画を足すだけで(ドロップダウンに並び)そのままプレビューも出る。

// パラメトリック(その場で 3D 計算する)動作の判定用。動画選択時はこれら以外になる。
const BASE_MOTIONS = new Set(["dolly_in","dolly_out","pan_left","pan_right","tilt_up","tilt_down",
  "truck_left","truck_right","pedestal_up","pedestal_down","roll_cw","roll_ccw",
  "zoom_in","zoom_out","orbit_cw","orbit_ccw","static"]);
const ALIAS_MOTIONS = new Set(["low_angle"]);
function isParametric(name) {
  if (ALIAS_MOTIONS.has(name)) return true;
  const toks = String(name || "").split("+").map((s) => s.trim()).filter(Boolean);
  return toks.length > 0 && toks.every((t) => BASE_MOTIONS.has(t) || ALIAS_MOTIONS.has(t));
}
// 動画モードで隠す widget: amount/hfov は完全に無関係、frames/width/height/fps は動画の値が使われる
const HIDE_IN_VIDEO = ["amount", "hfov", "frames", "width", "height", "fps", "scene", "props"];
function setWidgetHidden(w, hidden) {
  if (!w) return;
  if (hidden) {
    if (w.__b03type === undefined) { w.__b03type = w.type; w.__b03cs = w.computeSize; }
    w.type = "b03hidden";
    w.computeSize = () => [0, -4];
    w.hidden = true;
  } else if (w.__b03type !== undefined) {
    w.type = w.__b03type; w.computeSize = w.__b03cs;
    w.__b03type = undefined; w.__b03cs = undefined; w.hidden = false;
  }
}

app.registerExtension({
  name: "CameraReference3D.Preview",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "CameraReference3D") return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const ret = onNodeCreated ? onNodeCreated.apply(this, arguments) : undefined;

      // プレビュー用の video 要素
      const video = document.createElement("video");
      Object.assign(video, { loop: true, muted: true, autoplay: true, playsInline: true });
      Object.assign(video.style, {
        width: "100%", borderRadius: "6px", background: "#0e1017",
        objectFit: "contain", display: "block",
      });

      // ラベル(現在の motion 名)
      const wrap = document.createElement("div");
      const label = document.createElement("div");
      Object.assign(label.style, {
        font: "11px system-ui", color: "#9aa3b5", textAlign: "center",
        padding: "2px 0 4px",
      });
      wrap.appendChild(video);
      wrap.appendChild(label);

      this._b03 = { video, label };
      this.addDOMWidget("preview", "b03preview", wrap, { serialize: false, hideOnZoom: false });

      const motionWidget = () => this.widgets?.find((w) => w.name === "motion");
      const customWidget = () => this.widgets?.find((w) => w.name === "custom_motion");

      const resolveKey = () => {
        const cm = (customWidget()?.value || "").trim();
        if (cm) {
          // 複合は先頭成分でプレビュー(該当 mp4 が無ければ onerror で static)
          const first = cm.split("+")[0].trim();
          return { key: first || "static", label: cm + "（先頭でプレビュー）" };
        }
        const m = motionWidget()?.value || "orbit_cw";
        return { key: m, label: m };
      };

      // 読み込み失敗(該当 mp4 無し)は一度だけ static.mp4 にフォールバック
      video.addEventListener("error", () => {
        const fb = BASE + "static.mp4";
        if (!video.src.endsWith(fb)) video.src = fb;
      });

      this._b03update = () => {
        const { key, label: txt } = resolveKey();
        const src = BASE + encodeURIComponent(key) + ".mp4";
        if (!video.src.endsWith(src)) {
          video.src = src;
          const p = video.play && video.play();
          if (p && p.catch) p.catch(() => {});
        }

        // 動画モードか? = 実効選択がパラメトリックでない
        const cm = (customWidget()?.value || "").trim();
        const eff = cm || (motionWidget()?.value || "orbit_cw");
        const videoMode = !isParametric(eff);

        // amount/hfov/frames/width/height/fps を動画モードでは隠す
        let changed = false;
        for (const name of HIDE_IN_VIDEO) {
          const w = this.widgets?.find((x) => x.name === name);
          const before = w && w.type;
          setWidgetHidden(w, videoMode);
          if (w && w.type !== before) changed = true;
        }
        if (changed && this.computeSize) {
          const s = this.computeSize();
          this.setSize([Math.max(this.size[0], s[0]), s[1]]);
          this.setDirtyCanvas && this.setDirtyCanvas(true, true);
        }

        label.textContent = "▶ " + txt + (videoMode ? "  ·  動画のframes/解像度/fpsを使用" : "");
      };

      // widget の callback にフック
      for (const wname of ["motion", "custom_motion"]) {
        const w = this.widgets?.find((x) => x.name === wname);
        if (w) {
          const prev = w.callback;
          w.callback = function () {
            const r = prev ? prev.apply(this, arguments) : undefined;
            // 次フレームで反映(値確定後)
            requestAnimationFrame(() => app.graph?._nodes && updateSafe());
            return r;
          };
        }
      }
      const self = this;
      const updateSafe = () => { try { self._b03update(); } catch (e) {} };

      // 動画を web/previews/ にアップロードするボタン
      this.addWidget("button", "📤 動画をアップロード → previews", null, () => {
        const inp = document.createElement("input");
        inp.type = "file";
        inp.accept = "video/*";
        inp.style.display = "none";
        inp.onchange = async () => {
          const file = inp.files && inp.files[0];
          inp.remove();
          if (!file) return;
          const baseName = file.name.replace(/\.[^.]+$/, "");
          // 保存名を入力(変更可)。同名があればサーバ側で自動連番にする
          const name = window.prompt(
            "保存名(拡張子なし)。空欄ならファイル名を使用。\n同名が既にあれば自動で _1, _2 … を付けます。",
            baseName);
          if (name === null) return; // キャンセル
          const fd = new FormData();
          fd.append("file", file);
          fd.append("name", (name || "").trim());
          label.textContent = "⏳ アップロード中…";
          try {
            const resp = await fetch("/camera_reference_3d/upload", { method: "POST", body: fd });
            const j = await resp.json().catch(() => ({}));
            if (!resp.ok) {
              label.textContent = "✖ 失敗: " + (j.error || resp.status);
              console.error("[CameraReference3D] upload failed", j);
              return;
            }
            // motion ドロップダウンに足して即選択(ページ再読込なしで使える)
            const mw = motionWidget();
            if (mw && mw.options && Array.isArray(mw.options.values)) {
              if (!mw.options.values.includes(j.name)) mw.options.values.push(j.name);
              mw.value = j.name;
              if (typeof mw.callback === "function") mw.callback(j.name);
            }
            updateSafe();
            label.textContent = "▶ " + j.name +
              (j.renamed ? "（同名のため改名）" : "（アップロード完了）");
          } catch (e) {
            label.textContent = "✖ アップロードエラー";
            console.error("[CameraReference3D] upload error", e);
          }
        };
        document.body.appendChild(inp);
        inp.click();
      });

      updateSafe();
      // ノードサイズを少し広げてプレビューを見やすく
      const sz = this.computeSize ? this.computeSize() : null;
      if (sz) this.setSize([Math.max(this.size[0], 240), sz[1]]);

      return ret;
    };

    // ワークフロー読み込み時(configure)にもプレビューを復元
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
      if (this._b03update) requestAnimationFrame(() => { try { this._b03update(); } catch (e) {} });
      return r;
    };
  },
});
