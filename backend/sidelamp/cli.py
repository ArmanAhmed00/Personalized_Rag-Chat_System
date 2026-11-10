"""The ``sidelamp`` command."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser
from typing import Any

from sidelamp import APP_NAME, __version__


def _port_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _open_when_ready(url: str, health: str, timeout: float = 60.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(health, timeout=2.0).status_code == 200:
                webbrowser.open(url)
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_start(args: argparse.Namespace) -> int:
    import uvicorn

    from sidelamp.app import create_app
    from sidelamp.config import load_settings
    from sidelamp.errors import AppError
    from sidelamp.state import AppState

    overrides: dict[str, Any] = {}
    for key in ("host", "port", "library"):
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = value
    if args.no_browser:
        overrides["open_browser"] = False
    settings = load_settings(**overrides)
    _configure_logging(settings.log_level)

    if not _port_free(settings.host, settings.port):
        print(
            f"Port {settings.port} is already in use. If {APP_NAME} is already running, open "
            f"http://127.0.0.1:{settings.port}. Otherwise start it on another port, for example:\n"
            f"  sidelamp start --port {settings.port + 1}",
            file=sys.stderr,
        )
        return 2

    state = AppState(settings)
    try:
        state.open()
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 2

    app = create_app(state=state)
    shown_host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host  # noqa: S104
    url = f"http://{shown_host}:{settings.port}"
    print(f"{APP_NAME} {__version__} is running at {url}")
    print(f"Library: {settings.library}")
    print("Press Ctrl+C to stop.")
    if settings.open_browser:
        threading.Thread(
            target=_open_when_ready, args=(url, f"{url}/api/health"), daemon=True
        ).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning", access_log=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidelamp",
        description=f"{APP_NAME}: chat with your own documents, locally, with every claim cited.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    start = sub.add_parser("start", help="start the app and open it in your browser")
    start.add_argument("--host", help="address to listen on (default 127.0.0.1)")
    start.add_argument("--port", type=int, help="port to listen on (default 8642)")
    start.add_argument("--library", help="library folder to open")
    start.add_argument("--no-browser", action="store_true", help="don't open the browser")
    start.set_defaults(func=cmd_start)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    result: int = func(args)
    return result
