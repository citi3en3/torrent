"""Entry point.

Argument handling is deliberately tolerant: Windows invokes us with a bare path
or magnet URI when a file is opened, and with our own flags when the installer
runs registration, so both shapes have to work.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .app import Application, setup_logging
from .constants import APP_DISPLAY_NAME, APP_VERSION, is_frozen
from .ipc import send_to_primary

log = logging.getLogger(__name__)

ATTACH_PARENT_PROCESS = -1


def setup_streams() -> None:
    """Make stdout/stderr safe to write to, and use the parent console if any.

    A windowed PyInstaller build sets ``sys.stdout`` and ``sys.stderr`` to None.
    Anything that prints -- including argparse's own --version and --help --
    then dies with AttributeError, which PyInstaller surfaces as a modal
    traceback dialog: the app appears to hang with no way to see why.

    So: try to borrow the launching terminal's console (giving real output when
    run from PowerShell), and fall back to devnull so printing is always safe.
    Launched from Explorer or a shortcut there is no parent console, and the
    devnull fallback is exactly right.
    """
    if is_frozen():
        try:
            import ctypes

            if ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        except (AttributeError, OSError):
            pass

    sink = None
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            if sink is None:
                sink = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, sink)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_DISPLAY_NAME, description="BitTorrent client")
    parser.add_argument("sources", nargs="*", help=".torrent paths or magnet: URIs")
    parser.add_argument("--version", action="version", version=f"{APP_DISPLAY_NAME} {APP_VERSION}")
    parser.add_argument("--hidden", action="store_true", help="start minimised to the tray")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--register-shell",
        action="store_true",
        help="write the file/protocol associations and exit",
    )
    parser.add_argument(
        "--unregister-shell",
        action="store_true",
        help="remove the file/protocol associations and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --register-shell, print the registry writes without making them",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_streams()
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    setup_logging(args.verbose)

    from . import shell_integration

    if args.register_shell:
        for entry in shell_integration.register(dry_run=args.dry_run):
            print(entry)
        if args.dry_run:
            print("\n(dry run — nothing was written)")
        return 0

    if args.unregister_shell:
        shell_integration.unregister()
        print("Shell associations removed.")
        return 0

    # If an instance is already running, hand it our arguments and get out of
    # the way -- otherwise opening five files would start five sessions.
    if send_to_primary(args.sources):
        return 0

    app = Application(args.sources, start_hidden=args.hidden)
    try:
        return app.run()
    finally:
        app.quit()


if __name__ == "__main__":
    raise SystemExit(main())
