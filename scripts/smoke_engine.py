#!/usr/bin/env python3
"""Exercise the download engine with no GUI in the way.

Two runs against the same --data-dir prove the thing that actually matters:

    # first run: start downloading, stop at 3%
    python scripts/smoke_engine.py <magnet> --save-path D:\\tmp\\dl --data-dir D:\\tmp\\state --stop-at 0.03

    # second run: must come back at ~3%, not 0%, and not re-hash
    python scripts/smoke_engine.py --save-path D:\\tmp\\dl --data-dir D:\\tmp\\state --seconds 30

If the second run reports 0% or sits in "checking" for a long time, resume data
is broken -- fix that before touching anything else.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        help="magnet URI or path to a .torrent file (omit to only restore)",
    )
    parser.add_argument("--save-path", required=True, help="where files land")
    parser.add_argument("--data-dir", required=True, help="engine state directory")
    parser.add_argument("--seconds", type=float, default=90.0, help="run duration cap")
    parser.add_argument(
        "--stop-at",
        type=float,
        default=0.0,
        help="stop once progress reaches this fraction (0..1); 0 disables",
    )
    parser.add_argument("--port", type=int, default=0, help="listen port (0 = config default)")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    # Must be set before anything resolves a state path.
    os.environ["TORRENTAPP_DATA_DIR"] = str(Path(args.data_dir).resolve())

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    from torrentapp.config import Config
    from torrentapp.constants import ensure_dirs
    from torrentapp.engine import ResumeStore, TorrentEngine
    from torrentapp.engine.session import params_from_file, params_from_magnet, params_hash
    from torrentapp.util import format_bytes, format_eta, format_rate

    ensure_dirs()
    save_path = Path(args.save_path).resolve()
    save_path.mkdir(parents=True, exist_ok=True)

    config = Config.load()
    if args.port:
        config.listen_port = args.port

    store = ResumeStore()
    engine = TorrentEngine(config, store)
    engine.start()

    restored = engine.restore_torrents()
    print(f"[engine] started on port {config.listen_port}; restored {restored} torrent(s)")

    if args.source:
        known = store.known_hashes()
        if args.source.startswith("magnet:"):
            params = params_from_magnet(args.source, save_path)
        else:
            params = params_from_file(args.source, save_path)
        info_hash = params_hash(params)
        if info_hash and info_hash in known:
            print(f"[engine] {info_hash} already persisted -- restored, not re-added")
        else:
            engine.add(params)
            print(f"[engine] queued {info_hash or 'torrent'}")

    deadline = time.monotonic() + args.seconds
    peak_progress = 0.0
    exit_code = 0

    try:
        while time.monotonic() < deadline:
            engine.tick()
            for event in engine.poll():
                name = type(event).__name__
                if name == "TorrentsUpdated":
                    continue
                print(f"[event] {name}: {event}")

            items = engine.items()
            for item in items:
                peak_progress = max(peak_progress, item.progress)
                print(
                    f"  {item.name[:52]:<52} {item.state:<16}"
                    f" {item.progress * 100:6.2f}%"
                    f" ↓{format_rate(item.download_rate):>12}"
                    f" ↑{format_rate(item.upload_rate):>12}"
                    f" S:{item.num_seeds:<4} P:{item.num_peers:<4}"
                    f" ETA {format_eta(item.eta)}"
                )

            if args.stop_at and items:
                if all(i.progress >= args.stop_at for i in items):
                    print(f"[engine] reached stop-at={args.stop_at:.0%}; shutting down")
                    break

            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[engine] interrupted")
    finally:
        print("[engine] stopping (flushing resume data)...")
        started = time.monotonic()
        engine.stop(timeout=15.0)
        print(f"[engine] stopped in {time.monotonic() - started:.1f}s")

    resume_files = sorted(store.resume_dir.glob("*.fastresume"))
    torrent_files = sorted(store.torrent_dir.glob("*.torrent"))
    print(
        f"[state] {len(resume_files)} resume file(s), {len(torrent_files)} .torrent file(s)"
        f" in {store.resume_dir.parent}"
    )
    for path in resume_files:
        print(f"        {path.name}  {format_bytes(path.stat().st_size)}")

    if args.stop_at and peak_progress < args.stop_at:
        print(f"[warn] never reached {args.stop_at:.0%} (peaked at {peak_progress:.2%})")
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
