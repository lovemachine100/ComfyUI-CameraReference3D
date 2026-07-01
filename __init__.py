from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Splat camera nodes (RenderSplatPath / RenderSplatCinematic). Guarded: if the ComfyUI build
# lacks comfy_extras.nodes_gaussian_splat, these silently disable and CameraReference3D still loads.
try:
    from .render_splat import (
        NODE_CLASS_MAPPINGS as _SPLAT_MAPPINGS,
        NODE_DISPLAY_NAME_MAPPINGS as _SPLAT_DISPLAY,
    )
    NODE_CLASS_MAPPINGS.update(_SPLAT_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(_SPLAT_DISPLAY)
except Exception as _e:
    print("[ComfyUI-CameraReference3D] render_splat module failed to load (CameraReference3D unaffected): %s" % _e)

# フロントエンド拡張(motion 選択時のサンプル映像プレビュー)
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
