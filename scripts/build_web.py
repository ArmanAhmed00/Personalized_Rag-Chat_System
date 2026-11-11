"""Build the web app and copy it into the Python package.

    python scripts/build_web.py            # npm ci (if needed), npm run build, copy
    python scripts/build_web.py --check    # also run lint and the type check first

The Next.js static export in ``frontend/out`` is copied to ``backend/outlawz/web``, where
``outlawz start`` serves it on the same port as the API. Needs Node.js 20 or newer.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
OUT = FRONTEND / "out"
TARGET = ROOT / "backend" / "outlawz" / "web"


def npm(*args: str) -> None:
    exe = shutil.which("npm") or shutil.which("npm.cmd")
    if exe is None:
        sys.exit("npm wasn't found. Install Node.js 20 or newer from https://nodejs.org.")
    env = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}
    print(f"$ npm {' '.join(args)}", flush=True)
    subprocess.run([exe, *args], cwd=FRONTEND, env=env, check=True)  # noqa: S603


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="run lint and the type check first")
    parser.add_argument("--skip-install", action="store_true", help="don't run npm ci")
    args = parser.parse_args()

    if not args.skip_install and not (FRONTEND / "node_modules").is_dir():
        npm("ci", "--no-audit", "--no-fund")
    if args.check:
        npm("run", "lint")
        npm("run", "typecheck")
    if OUT.exists():
        shutil.rmtree(OUT)
    npm("run", "build")
    if not (OUT / "index.html").is_file():
        sys.exit("The build didn't produce frontend/out/index.html.")

    staging = TARGET.with_name(TARGET.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(OUT, staging)
    if TARGET.exists():
        shutil.rmtree(TARGET)
    staging.rename(TARGET)
    files = sum(1 for p in TARGET.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file())
    print(f"Copied {files} files ({size / 1024 / 1024:.1f} MB) to {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
