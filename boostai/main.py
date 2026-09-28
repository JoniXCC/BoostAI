"""BoostAI entry point.

    boostai                     launch the desktop app
    boostai --minimized         start in the system tray
    boostai --scan              run a read-only scan in the console and print the findings
    boostai --elevated-helper F (internal) run one whitelisted admin action from request file F
"""

from __future__ import annotations

import argparse
import sys

from boostai import __version__


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def console_scan() -> int:
    from boostai.core.engine import BoostEngine
    from boostai.core.models import fmt_bytes

    engine = BoostEngine(start_monitor=True)
    try:
        print("Warming up the monitor (8 s)...")
        import time

        time.sleep(8)
        result = engine.run_scan("quick", lambda p, m: print(f"  [{p:3d}%] {m}"))
        snap = result.snapshot
        print(f"\nHealth: {result.score.overall:.0f}/100 ({result.score.label})")
        for name, cat in result.score.categories.items():
            print(f"  {name:<16} {cat.score:5.0f}   {'; '.join(cat.details)}")
        print(f"\nCPU {snap.cpu.total_percent:.0f}% | RAM {snap.memory.percent:.0f}% "
              f"({fmt_bytes(snap.memory.used)} / {fmt_bytes(snap.memory.total)}) | pressure "
              f"{snap.memory_pressure.value} | {snap.process_count} processes")
        print(f"\n{len(result.issues)} finding(s):")
        for issue in result.issues:
            print(f"\n[{issue.severity.value}/{issue.confidence.value}] {issue.title}  ({issue.root_cause.value})")
            for line in issue.evidence:
                print("   - " + line.replace("\n", "\n     "))
            for p in issue.proposals:
                print(f"   -> suggested (needs approval in the app): {p.label} [{p.action_id}]")
        print("\nNothing was changed. Open the BoostAI app to review and approve fixes.")
        return 0
    finally:
        engine.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boostai", description="BoostAI - safe PC performance diagnostics")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    parser.add_argument("--scan", action="store_true", help="run a read-only console scan")
    parser.add_argument("--elevated-helper", metavar="REQUEST", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"BoostAI {__version__}")
    args = parser.parse_args(argv)

    _load_env()
    from boostai.config.logging_config import configure_logging

    if args.elevated_helper:
        from boostai.actions.elevated_helper import run_helper

        return run_helper(args.elevated_helper)
    configure_logging()
    if args.scan:
        return console_scan()
    from boostai.ui.app import run_gui

    return run_gui(minimized=args.minimized)


if __name__ == "__main__":
    sys.exit(main())
