"""Load test: virtual users upload, search, analyse and chat at the same time.

Uses only asyncio and httpx. Without ``--url`` it starts its own Outlawz on a free port, with the
fake model and fake embeddings in a temporary library, so it measures Outlawz itself and needs no
model server. Reports requests per second and p50/p95/p99 per action, and can fail CI on
thresholds.

    python scripts/loadtest.py --users 8 --duration 20
    python scripts/loadtest.py --url http://127.0.0.1:8642 --token "$TOKEN" \
        --fail-p95 1500 --min-rps 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import httpx

HEADERS = {"X-Outlawz": "1"}
WEIGHTS = {"search": 40, "chat": 30, "analyse": 20, "upload": 10}
QUESTIONS = [
    "How long is the refund window?",
    "What does express shipping cost?",
    "Which region sold the most?",
    "When is the office open?",
]
CSV = "region,channel,units,revenue\n" + "".join(
    f"{r},{c},{u},{u * 9.5:.2f}\n"
    for r in ("North", "South", "East", "West")
    for c in ("Online", "Store")
    for u in range(1, 26)
)


def tiny_pdf(text: str) -> bytes:
    """A one-page PDF with a line of text, built by hand (no PDF library needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    data = sorted(values)
    k = (len(data) - 1) * q
    low, high = int(k), min(int(k) + 1, len(data) - 1)
    return round(data[low] + (data[high] - data[low]) * (k - low), 1)


class Stats:
    def __init__(self) -> None:
        self.times: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, int] = defaultdict(int)

    def add(self, action: str, ms: float, ok: bool) -> None:
        self.times[action].append(ms)
        if not ok:
            self.errors[action] += 1


async def timed(stats: Stats, action: str, request: object) -> httpx.Response | None:
    started = time.perf_counter()
    try:
        response = await request  # type: ignore[misc]
    except httpx.HTTPError:
        stats.add(action, (time.perf_counter() - started) * 1000, False)
        return None
    ok = response.status_code < 400
    stats.add(action, (time.perf_counter() - started) * 1000, ok)
    return response


async def user(client: httpx.AsyncClient, stats: Stats, csv_id: str, until: float, n: int) -> None:
    rng = random.Random(n)
    chat_id: str | None = None
    uploads = 0
    actions, weights = list(WEIGHTS), list(WEIGHTS.values())
    while time.monotonic() < until:
        action = rng.choices(actions, weights)[0]
        if action == "search":
            await timed(
                stats, "search", client.post("/api/search", json={"query": rng.choice(QUESTIONS)})
            )
        elif action == "analyse":
            plan = {
                "op": "aggregate",
                "agg": rng.choice(["sum", "mean", "max"]),
                "column": "revenue",
                "by": ["region"],
            }
            await timed(
                stats,
                "analyse",
                client.post("/api/analysis/run", json={"doc_id": csv_id, "plan": plan}),
            )
        elif action == "chat":
            if chat_id is None:
                created = await timed(stats, "chat", client.post("/api/chats", json={}))
                chat_id = (
                    created.json()["id"]
                    if created is not None and created.status_code == 201
                    else None
                )
                continue
            await timed(
                stats,
                "chat",
                client.post(
                    f"/api/chats/{chat_id}/ask?stream=false",
                    json={"question": rng.choice(QUESTIONS)},
                ),
            )
        else:
            uploads += 1
            files = {
                "file": (
                    f"load-{n}-{uploads}.pdf",
                    tiny_pdf(f"Load test document {n}-{uploads}. Refunds take 30 days."),
                )
            }
            await timed(stats, "upload", client.post("/api/documents", files=files))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(folder: Path) -> tuple[subprocess.Popen[bytes], str]:
    port = free_port()
    env = {
        **os.environ,
        "OUTLAWZ_LLM": "fake",
        "OUTLAWZ_EMBEDDER": "fake",
        "OUTLAWZ_RERANKER": "lexical",
        "OUTLAWZ_DATA_DIR": str(folder / "data"),
        "OUTLAWZ_BACKUP_EVERY": "off",
        "OUTLAWZ_LOG_LEVEL": "warning",
    }
    command = [
        sys.executable,
        "-m",
        "outlawz",
        "start",
        "--no-browser",
        "--port",
        str(port),
        "--library",
        str(folder / "library"),
    ]
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)  # noqa: S603
    return process, f"http://127.0.0.1:{port}"


async def wait_healthy(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(base_url=url, timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                if (await client.get("/api/health")).json().get("status") == "ok":
                    return
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(0.3)
    raise SystemExit(f"Outlawz didn't start at {url}")


async def run(args: argparse.Namespace) -> dict[str, object]:
    headers = dict(HEADERS)
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    limits = httpx.Limits(max_connections=args.users * 2)
    async with httpx.AsyncClient(
        base_url=args.url, headers=headers, timeout=60.0, limits=limits
    ) as client:
        csv = await client.post("/api/documents", files={"file": ("loadtest.csv", CSV.encode())})
        if csv.status_code != 201:
            raise SystemExit(f"Couldn't upload the test table: {csv.status_code} {csv.text[:200]}")
        await client.post(
            "/api/documents",
            files={
                "file": (
                    "seed.pdf",
                    tiny_pdf("Refunds take 30 days. Express shipping costs 9 dollars."),
                )
            },
        )
        await asyncio.sleep(2.0)  # let the seed documents be read
        stats = Stats()
        started = time.monotonic()
        until = started + args.duration
        await asyncio.gather(
            *(user(client, stats, csv.json()["id"], until, n) for n in range(args.users))
        )
        elapsed = time.monotonic() - started
    total = sum(len(v) for v in stats.times.values())
    report: dict[str, object] = {
        "users": args.users,
        "seconds": round(elapsed, 1),
        "requests": total,
        "rps": round(total / elapsed, 1) if elapsed else 0,
        "errors": sum(stats.errors.values()),
        "actions": {
            action: {
                "count": len(times),
                "errors": stats.errors.get(action, 0),
                "p50": percentile(times, 0.50),
                "p95": percentile(times, 0.95),
                "p99": percentile(times, 0.99),
            }
            for action, times in sorted(stats.times.items())
        },
    }
    all_times = [t for times in stats.times.values() for t in times]
    report["p95"] = percentile(all_times, 0.95)
    return report


def print_report(report: dict[str, object]) -> None:
    print(
        f"\n{report['requests']} requests from {report['users']} users in {report['seconds']} s "
        f"= {report['rps']} requests/s, {report['errors']} errors\n"
    )
    print(f"  {'action':<10}{'count':>8}{'errors':>8}{'p50 ms':>10}{'p95 ms':>10}{'p99 ms':>10}")
    actions = report["actions"]
    assert isinstance(actions, dict)
    for name, a in actions.items():
        timings = "".join(f"{a[k]!s:>10}" for k in ("p50", "p95", "p99"))
        print(f"  {name:<10}{a['count']:>8}{a['errors']:>8}{timings}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", help="a running Outlawz (default: start one with the fake model)")
    parser.add_argument("--token", help="access token, if the server needs one")
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--duration", type=float, default=20.0, help="seconds")
    parser.add_argument("--fail-p95", type=float, help="fail if the overall p95 is above this (ms)")
    parser.add_argument("--min-rps", type=float, help="fail if requests/s is below this")
    parser.add_argument("--max-errors", type=int, default=0, help="fail above this many errors")
    parser.add_argument("--json", help="also write the report to this file")
    args = parser.parse_args()

    server = None
    temp = None
    if not args.url:
        temp = tempfile.TemporaryDirectory(prefix="outlawz-load-")
        server, args.url = start_server(Path(temp.name))
    try:
        if server is not None:
            asyncio.run(wait_healthy(args.url))
        report = asyncio.run(run(args))
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
        if temp is not None:
            temp.cleanup()
    print_report(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    failures = []
    if args.fail_p95 is not None and (report["p95"] or 0) > args.fail_p95:  # type: ignore[operator]
        failures.append(f"p95 {report['p95']} ms is above {args.fail_p95} ms")
    if args.min_rps is not None and float(report["rps"]) < args.min_rps:  # type: ignore[arg-type]
        failures.append(f"{report['rps']} requests/s is below {args.min_rps}")
    if int(report["errors"]) > args.max_errors:  # type: ignore[call-overload]
        failures.append(f"{report['errors']} errors (allowed: {args.max_errors})")
    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
