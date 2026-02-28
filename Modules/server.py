from __future__ import annotations

from Modules.plugin_server import start_server as _start_plugin_server
from Modules.plugin_server import stop_server as _stop_plugin_server

DEFAULT_PORT = 7022


def start_server(port: int = DEFAULT_PORT) -> int:
    return _start_plugin_server(port=port)


def stop_server(port: int = DEFAULT_PORT) -> None:
    _ = port
    _stop_plugin_server()
