#!/usr/bin/env python3
"""Supervise the Band 6 -> upstream mirror stack.

Wires ``band_bridge.py`` stdout directly into ``mirror_band.py`` stdin, which
is how the upstream mirror's documented ``--stdin`` mode is meant to be used.
Using a supervisor instead of a shell pipe keeps the packet stream byte-clean
and shows both processes' diagnostics in one console window.

Exit codes: the mirror's exit code, or 130 after Ctrl+C.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
DEFAULT_RECEIVER = "http://127.0.0.1:8000"
DEFAULT_PORT = 8777


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run-stack",
        description="Run the Band 6 bridge into the upstream KINETIQ mirror.",
    )
    parser.add_argument("--kinetiq-repo", type=Path,
                        default=HERE.parent / "external" / "KinetiQ",
                        help="path to the upstream KINETIQ clone")
    parser.add_argument("--receiver", default=DEFAULT_RECEIVER,
                        help=f"KinetiQ receiver base URL (default {DEFAULT_RECEIVER})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"preferred mirror port (default {DEFAULT_PORT})")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    common = [
        "--receiver", args.receiver,
        "--kinetiq-repo", str(args.kinetiq_repo.resolve()),
    ]

    bridge = subprocess.Popen(
        [sys.executable, str(HERE / "band_bridge.py"), *common],
        stdout=subprocess.PIPE,
        bufsize=0,
    )
    assert bridge.stdout is not None
    mirror = subprocess.Popen(
        [sys.executable, str(HERE / "mirror_band.py"), *common,
         "--port", str(args.port)],
        stdin=bridge.stdout,
    )
    # The parent's copy must close so stdin reaches EOF if the bridge dies.
    bridge.stdout.close()

    code = 0
    try:
        code = mirror.wait()
    except KeyboardInterrupt:
        code = 130
    finally:
        for process in (mirror, bridge):
            if process.poll() is None:
                process.terminate()
        for process in (mirror, bridge):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
