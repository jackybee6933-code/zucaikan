import json
from pathlib import Path


ASSETS = Path(__file__).resolve().parent / "assets" / "pwa"
MANIFEST = {
    "id": "/",
    "name": "足彩分析系统",
    "short_name": "足彩分析",
    "lang": "zh-CN",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f3f5f7",
    "theme_color": "#16705a",
    "icons": [
        {"src": f"/pwa/icon-{size}.png", "sizes": f"{size}x{size}",
         "type": "image/png", "purpose": "any maskable"}
        for size in (192, 512)
    ],
}
STATIC_ROUTES = {
    "/service-worker.js": ("service-worker.js", "application/javascript; charset=utf-8"),
    "/pwa/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/pwa/offline.html": ("offline.html", "text/html; charset=utf-8"),
    "/pwa/icon-192.png": ("icon-192.png", "image/png"),
    "/pwa/icon-512.png": ("icon-512.png", "image/png"),
}


def serve_pwa_asset(handler, path: str) -> bool:
    if path == "/manifest.webmanifest":
        data = json.dumps(MANIFEST, ensure_ascii=False).encode("utf-8")
        content_type = "application/manifest+json; charset=utf-8"
    elif path in STATIC_ROUTES:
        filename, content_type = STATIC_ROUTES[path]
        data = (ASSETS / filename).read_bytes()
    else:
        return False
    handler._cache_policy = "no-cache"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    if path == "/service-worker.js":
        handler.send_header("Service-Worker-Allowed", "/")
    handler.end_headers()
    handler.wfile.write(data)
    return True
