"""
DAMaya Agent Launcher
系统托盘启动器 — 使用 pystray + Pillow
安装依赖: pip install pystray Pillow
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("缺少依赖，请先运行: pip install pystray Pillow")
    sys.exit(1)

# ── 路径 & 配置 ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = PROJECT_ROOT / "server_web.py"

# 读取 settings.json 来获取 host/port（最简解析，不导入整个 Client）
try:
    import json
    _s = json.loads((PROJECT_ROOT / "settings.json").read_text(encoding="utf-8"))
    HOST = _s.get("server_host", "127.0.0.1")
    PORT = int(_s.get("server_port", 8000))
except Exception:
    HOST = "127.0.0.1"
    PORT = 8000

SERVER_URL = f"http://{HOST}:{PORT}"

# ── Server Process ──────────────────────────────────────────────
_server_proc: subprocess.Popen | None = None
_server_lock = threading.Lock()


def _is_server_up() -> bool:
    """轮询服务是否就绪。"""
    import urllib.request
    try:
        urllib.request.urlopen(SERVER_URL, timeout=1)
        return True
    except Exception:
        return False


def start_server() -> None:
    global _server_proc
    with _server_lock:
        if _server_proc and _server_proc.poll() is None:
            return  # 已在运行
        _server_proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    print(f"[Launcher] 服务器已启动 (PID {_server_proc.pid})")


def stop_server() -> None:
    global _server_proc
    with _server_lock:
        if _server_proc and _server_proc.poll() is None:
            _server_proc.terminate()
            try:
                _server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _server_proc.kill()
            print("[Launcher] 服务器已停止")
        _server_proc = None


def open_browser() -> None:
    """等待服务就绪后自动打开浏览器。"""
    def _wait_and_open():
        for _ in range(30):        # 最多等 15s
            if _is_server_up():
                break
            time.sleep(0.5)
        webbrowser.open(SERVER_URL)

    threading.Thread(target=_wait_and_open, daemon=True).start()


# ── Tray Icon Image ─────────────────────────────────────────────
def _make_icon_image(size=64) -> Image.Image:
    """程序化生成简单托盘图标（深色背景 + 蓝色菱形）。"""
    img = Image.new("RGBA", (size, size), (9, 9, 11, 255))
    d   = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    r = size // 3
    # Hexagon-like shape
    pts = []
    import math
    for i in range(6):
        angle = math.radians(30 + 60 * i)
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    d.polygon(pts, fill=(59, 130, 246, 255))
    # Inner dot
    ir = size // 8
    d.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=(255, 255, 255, 220))
    return img


# Try to load a real icon from launcher/icon.png if it exists
_icon_path = Path(__file__).parent / "icon.png"
if _icon_path.exists():
    try:
        _tray_image = Image.open(_icon_path).resize((64, 64))
    except Exception:
        _tray_image = _make_icon_image()
else:
    _tray_image = _make_icon_image()


# ── Tray Menu Actions ───────────────────────────────────────────
def _action_open(icon, item):
    if not _is_server_up():
        start_server()
    open_browser()


def _action_restart(icon, item):
    stop_server()
    start_server()
    open_browser()


def _action_quit(icon, item):
    stop_server()
    icon.stop()


# ── Main ────────────────────────────────────────────────────────
def main():
    # Start server immediately
    start_server()
    open_browser()

    menu = pystray.Menu(
        pystray.MenuItem("🌐 打开 Web UI",   _action_open, default=True),
        pystray.MenuItem("🔄 重启服务器",     _action_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("❌ 退出",           _action_quit),
    )

    icon = pystray.Icon(
        name="DAMaya Agent",
        icon=_tray_image,
        title="DAMaya Agent",
        menu=menu,
    )

    print(f"[Launcher] 托盘已启动，访问: {SERVER_URL}")
    icon.run()


if __name__ == "__main__":
    main()
