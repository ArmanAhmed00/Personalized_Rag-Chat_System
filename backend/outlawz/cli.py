"""The ``outlawz`` command."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from outlawz import APP_NAME, __version__


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

    from outlawz.app import create_app
    from outlawz.config import load_settings
    from outlawz.errors import AppError
    from outlawz.state import AppState

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
            f"  outlawz start --port {settings.port + 1}",
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


def _pairs(items: list[str] | None) -> dict[str, float]:
    out = {}
    for item in items or []:
        key, _, value = item.rpartition("=")
        if not key:
            raise SystemExit(
                f"Use KEY=VALUE, for example retrieval.hybrid.hit@5=0.8 (got {item!r})"
            )
        out[key] = float(value)
    return out


def cmd_eval(args: argparse.Namespace) -> int:
    """Run the evaluation suites and print a report. Exits with 1 below any threshold."""
    import asyncio
    import json
    import tempfile

    from outlawz.config import load_settings
    from outlawz.errors import AppError
    from outlawz.evals.runner import check_thresholds, report
    from outlawz.state import AppState

    minimums, maximums = _pairs(args.min), _pairs(args.max)
    temp = None
    if args.library:
        library = args.library
    else:
        temp = tempfile.TemporaryDirectory(prefix="outlawz-eval-")
        library = temp.name
    settings = load_settings(library=library, open_browser=False)
    _configure_logging("warning")
    state = AppState(settings)
    try:
        state.open()
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 2

    async def go() -> dict[str, Any]:
        try:
            return await state.evals.run(
                args.suite or None,
                progress=lambda m: print(f"… {m}", file=sys.stderr),
                save=bool(args.library),
            )
        finally:
            await state.close()

    try:
        record = asyncio.run(go())
    finally:
        if temp is not None:
            temp.cleanup()
    print(report(record))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, default=str)
    failures = check_thresholds(record, minimums, maximums)
    if failures:
        print("\nBelow thresholds:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


def _settings_for(args: argparse.Namespace) -> Any:
    from outlawz.config import load_settings

    overrides: dict[str, Any] = {"open_browser": False}
    if getattr(args, "library", None):
        overrides["library"] = args.library
    return load_settings(**overrides)


def cmd_doctor(args: argparse.Namespace) -> int:
    from outlawz.doctor import print_report, run_checks

    settings = _settings_for(args)
    print(f"{APP_NAME} {__version__}: checking your setup\n")
    return print_report(run_checks(settings))


def cmd_backup(args: argparse.Namespace) -> int:
    from outlawz.backups import create_backup

    settings = _settings_for(args)
    if not settings.library_paths.app.exists():
        print(f"There's no library at {settings.library} yet.", file=sys.stderr)
        return 2
    out = args.out or settings.data_paths.backups_dir
    path = create_backup(
        settings.library,
        Path(out),
        include_documents=args.documents,
        include_vectors=not args.no_vectors,
    )
    print(f"Backup saved: {path} ({path.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from outlawz.backups import verify_backup

    report = verify_backup(Path(args.file))
    print(report.message)
    return 0 if report.ok else 1


def cmd_restore(args: argparse.Namespace) -> int:
    from outlawz.backups import restore_backup
    from outlawz.errors import AppError
    from outlawz.store.lock import InstanceLock

    settings = _settings_for(args)
    settings.library.mkdir(parents=True, exist_ok=True)
    settings.library_paths.app.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(settings.library_paths.lock_file)
    try:
        lock.acquire()
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    try:
        lock.release()  # the restore swaps the app folder (and the lock file in it)
        report = restore_backup(
            Path(args.file),
            settings.library,
            replace=args.replace,
            safety_dir=settings.data_paths.backups_dir,
        )
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Restored {report.files:,} files into {settings.library}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="outlawz",
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

    ev = sub.add_parser("eval", help="run the evaluation suites and print a report")
    ev.add_argument(
        "--suite",
        action="append",
        choices=["chunking", "retrieval", "answers", "data"],
        help="run only this suite (repeatable)",
    )
    ev.add_argument(
        "--library", help="use this library (saves the run there); default: a temporary one"
    )
    ev.add_argument("--json", help="also write the full results to this file")
    ev.add_argument(
        "--min",
        action="append",
        metavar="KEY=VALUE",
        help="fail if a result is below VALUE, e.g. retrieval.hybrid+rerank.hit@5=0.8",
    )
    ev.add_argument(
        "--max",
        action="append",
        metavar="KEY=VALUE",
        help="fail if a result is above VALUE, e.g. answers.made_up_numbers=0",
    )
    ev.set_defaults(func=cmd_eval)

    doctor = sub.add_parser("doctor", help="check the setup and say how to fix problems")
    doctor.add_argument("--library", help="library folder to check")
    doctor.set_defaults(func=cmd_doctor)

    backup = sub.add_parser("backup", help="back up the library's app folder")
    backup.add_argument("--library", help="library folder to back up")
    backup.add_argument("--documents", action="store_true", help="include the documents too")
    backup.add_argument(
        "--no-vectors", action="store_true", help="leave out search vectors (rebuilt after restore)"
    )
    backup.add_argument("--out", help="folder to save the backup in (default: the OS data folder)")
    backup.set_defaults(func=cmd_backup)

    verify = sub.add_parser("verify", help="check a backup against its manifest")
    verify.add_argument("file", help="the .tar.gz backup")
    verify.set_defaults(func=cmd_verify)

    restore = sub.add_parser("restore", help="restore a backup (Outlawz must not be running)")
    restore.add_argument("file", help="the .tar.gz backup")
    restore.add_argument("--library", help="library folder to restore into")
    restore.add_argument(
        "--replace", action="store_true", help="replace a library that isn't empty"
    )
    restore.set_defaults(func=cmd_restore)
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
