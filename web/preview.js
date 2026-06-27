import { app } from "../../scripts/app.js";

// B03 Camera Reference: motion を選ぶとノード上にその動きのサンプル映像をプレビュー表示
const PKG = "ComfyUI-CameraReference3D";
const BASE = `/extensions/${PKG}/previews/`;
// 固定リストは持たない: 選択名の <name>.mp4 を素直に読み、無ければ static.mp4 にフォールバック。
// これで previews/ に動画を足すだけで(ドロップダウンに並び)そのままプレビューも出る。

app.registerExtension({
  name: "B03.CameraReferencePreview",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "B03CameraReferenceGenerator") return;

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
        label.textContent = "▶ " + txt;
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
