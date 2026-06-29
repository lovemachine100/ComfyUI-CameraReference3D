import { app } from "../../scripts/app.js";


// Camera Reference (3D): motion を選ぶとノード上にその動きのサンプル映像をプレビュー表示
const PKG = "ComfyUI-CameraReference3D";
const BASE = `/extensions/${PKG}/previews/`;
// 固定リストは持たない: 選択名の <name>.mp4 を素直に読み、無ければ static.mp4 にフォールバック。
// これで previews/ に動画を足すだけで(ドロップダウンに並び)そのままプレビューも出る。

// 常に非表示にする項目(ユーザー設定: 使っていない高度な項目)。
// 表示する基本: motion / width / height / frames / fps / プレビュー / アップロードボタン。
const HIDE_ALWAYS = ["amount", "hfov", "scene", "props", "custom_motion"];
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

      const self = this;
      const motionWidget = () => self.widgets?.find((w) => w.name === "motion");
      const customWidget = () => self.widgets?.find((w) => w.name === "custom_motion");
      // widget の値をセット(UI に反映)。同値ならスキップ。
      const setWidgetValue = (name, val) => {
        const w = self.widgets?.find((x) => x.name === name);
        if (!w || val === undefined || val === null || w.value === val) return;
        w.value = val;
        try { if (typeof w.callback === "function") w.callback(val); } catch (e) {}
      };
      // 使っていない項目(amount/hfov/scene/props/custom_motion)を常に非表示にする
      for (const name of HIDE_ALWAYS) setWidgetHidden(self.widgets?.find((x) => x.name === name), true);

      // プレビューは「固定高さの箱 + overflow:hidden」に入れる。
      // → 縦長動画でも箱の中で letterbox され、絶対にノードを突き破らない。
      const PREVIEW_BOX_H = 200; // 動画表示ボックスの固定高さ(px)
      const video = document.createElement("video");
      Object.assign(video, { loop: true, muted: true, autoplay: true, playsInline: true });
      Object.assign(video.style, { width: "100%", height: "100%", objectFit: "contain", display: "block" });

      const box = document.createElement("div");
      Object.assign(box.style, {
        position: "relative", width: "100%", height: PREVIEW_BOX_H + "px", overflow: "hidden",
        borderRadius: "6px", background: "#0e1017", boxSizing: "border-box",
      });

      // ラベル(解像度/長さ)は動画ボックスの下部に「重ねて」表示する。
      // ボックスは描画確認済 → 中に入れれば新フロントでもクリップされず確実に見える。
      const label = document.createElement("div");
      Object.assign(label.style, {
        position: "absolute", left: "0", right: "0", bottom: "0",
        font: "11px system-ui", color: "#fff", textAlign: "center",
        padding: "3px 5px", background: "rgba(0,0,0,0.62)", boxSizing: "border-box",
        lineHeight: "1.25", wordBreak: "break-all", pointerEvents: "none",
      });
      box.appendChild(video);
      box.appendChild(label);

      const wrap = document.createElement("div");
      Object.assign(wrap.style, { width: "100%", boxSizing: "border-box" });
      wrap.appendChild(box);

      this._b03 = { video, label, box };
      const previewWidget = this.addDOMWidget("preview", "b03preview", wrap, { serialize: false, hideOnZoom: false });
      // DOM ウィジェットの高さを固定 → ノードがこの分の高さを必ず確保し、はみ出さない。
      const PREVIEW_TOTAL_H = PREVIEW_BOX_H + 8;
      const NODE_W = 260; // コンパクトなノード幅(間延び防止)
      if (previewWidget) {
        // 希望幅を固定の NODE_W にする(現在幅追従だと一度広がると戻らず間延びする)
        previewWidget.computeSize = () => [NODE_W, PREVIEW_TOTAL_H];
        try { Object.assign(previewWidget, { height: PREVIEW_TOTAL_H }); } catch (e) {}
      }

      // ノードを widget 構成に合わせてコンパクトに収める(幅も詰める)
      const fitNode = () => {
        requestAnimationFrame(() => {
          try {
            if (!self.computeSize) return;
            const s = self.computeSize();
            self.setSize([Math.max(NODE_W, s[0]), s[1]]);
            self.setDirtyCanvas && self.setDirtyCanvas(true, true);
          } catch (e) {}
        });
      };
      video.addEventListener("loadeddata", fitNode);
      self.__b03fitNode = fitNode; // onConfigure からも呼べるよう保持

      // previews 動画のメタ(解像度・フレーム数・fps)を取得(キャッシュ)
      const metaCache = {};
      const fetchMeta = async (stem) => {
        if (metaCache[stem]) return metaCache[stem];
        try {
          const r = await fetch("/camera_reference_3d/meta?name=" + encodeURIComponent(stem));
          const j = await r.json();
          if (r.ok && !j.error) { metaCache[stem] = j; return j; }
        } catch (e) {}
        return null;
      };

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

      this._b03update = (opts) => {
        opts = opts || {};
        const { key, label: txt } = resolveKey();
        const src = BASE + encodeURIComponent(key) + ".mp4";
        if (!video.src.endsWith(src)) {
          video.src = src;
          const p = video.play && video.play();
          if (p && p.catch) p.catch(() => {});
        }

        // 「今プレビューに表示している動画そのもの」の解像度/長さを取得し、
        // ★ width/height 入力欄にその値をセットする(= 入力欄が表示中動画の解像度に変わる)。
        // ただしユーザー操作(motion 変更)のときだけ。保存ワークフロー復元時は上書きしない。
        const userChanged = !!opts.userChanged;
        label.textContent = "▶ " + txt + "  ·  読み込み中…";
        fetchMeta(key).then((meta) => {
          if (!meta) {
            label.textContent = "▶ " + txt;
            return;
          }
          const fpsR = meta.fps ? Math.round(meta.fps) : "?";
          const spec = `${meta.width}×${meta.height} / ${meta.frames}f / ${fpsR}fps`;
          label.textContent = `▶ ${txt}  ·  ${spec}`;
          if (userChanged) {
            // 入力欄を「表示中動画の値」に合わせる:
            //   width/height = 解像度, frames = 全体フレーム数, fps = フレームレート
            //   (pan_right→220×220/49f/24fps, アップロード→704×1280/121f/25fps)
            setWidgetValue("width", meta.width);
            setWidgetValue("height", meta.height);
            if (meta.frames > 0) setWidgetValue("frames", meta.frames);
            if (meta.fps > 0) setWidgetValue("fps", Math.round(meta.fps * 100) / 100);
            self.setDirtyCanvas && self.setDirtyCanvas(true, true);
          }
          const g = (nm) => self.widgets?.find((x) => x.name === nm)?.value;
        });
      };

      const updateSafe = (opts) => { try { self._b03update(opts); } catch (e) {} };

      // (A) widget の callback にフック(litegraph 経路)
      for (const wname of ["motion", "custom_motion"]) {
        const w = this.widgets?.find((x) => x.name === wname);
        if (w) {
          const prev = w.callback;
          w.callback = function () {
            const r = prev ? prev.apply(this, arguments) : undefined;
            requestAnimationFrame(() => updateSafe({ userChanged: true }));
            return r;
          };
        }
      }

      // (B) value セッターにもフック。ComfyUI 0.25 では combo 変更が callback(A)を経由せず
      //     .value を直接代入する経路があり、(A) だけだと「選択しても変わらない」ことがある。
      //     復元(configure)中は __b03restoring=true なので userChanged=false → width/height を上書きしない。
      const installValueWatcher = (w) => {
        if (!w || w.__b03watched) return;
        w.__b03watched = true;
        let _v = w.value;
        try {
          Object.defineProperty(w, "value", {
            configurable: true,
            enumerable: true,
            get() { return _v; },
            set(nv) {
              const changed = nv !== _v;
              _v = nv;
              if (changed) {
                const uc = !self.__b03restoring;
                requestAnimationFrame(() => updateSafe({ userChanged: uc }));
              }
            },
          });
        } catch (e) {}
      };
      installValueWatcher(motionWidget());
      installValueWatcher(customWidget());

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
            updateSafe({ userChanged: true });
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
      fitNode(); // 作成時にコンパクト幅へ収める

      return ret;
    };

    // ワークフロー復元中は __b03restoring を立て、値セッター(B)が userChanged 扱いで
    // width/height を上書きしないようにする(保存値を尊重)。
    const origConfigure = nodeType.prototype.configure;
    nodeType.prototype.configure = function () {
      this.__b03restoring = true;
      try {
        return origConfigure ? origConfigure.apply(this, arguments) : undefined;
      } finally {
        const node = this;
        requestAnimationFrame(() => { node.__b03restoring = false; });
      }
    };

    // ワークフロー読み込み時(configure)にもプレビュー/ラベルを復元(userChanged なし=値は尊重)
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure ? onConfigure.apply(this, arguments) : undefined;
      const node = this;
      if (this._b03update) requestAnimationFrame(() => {
        try { node._b03update(); } catch (e) {}
        // 保存時に間延びした幅もコンパクトに収め直す
        try { node.__b03fitNode && node.__b03fitNode(); } catch (e) {}
      });
      return r;
    };
  },
});
