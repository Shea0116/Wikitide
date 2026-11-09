"""Independent durable worker. Run with the same database/config as the web app."""
import argparse
import os
from pathlib import Path
import signal
import threading


def main(argv=None):
    parser = argparse.ArgumentParser(description='WikiTide durable account task worker')
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--content', type=Path, required=True)
    parser.add_argument('--once', action='store_true', help='Handle one task and exit')
    parser.add_argument('--poll-seconds', type=float, default=2)
    args = parser.parse_args(argv)
    if not args.db.is_file(): parser.error('The shared site database must already exist')
    if not .1 <= args.poll_seconds <= 60: parser.error('poll-seconds must be between 0.1 and 60')
    # app.py has a module-level ASGI app: bind its environment BEFORE importing.
    os.environ['KB_DB_PATH'] = str(args.db.resolve())
    os.environ['KB_CONTENT_ROOT'] = str(args.content.resolve())
    from app import app
    if not app.state.agent_runtime_enabled: parser.error('KB_AGENT_RUNTIME_ENABLED must be enabled')
    service, stop = app.state.agent_runtime, threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if args.once:
        service.run_once(); return 0
    while not stop.is_set():
        if not service.run_once(): stop.wait(args.poll_seconds)
    return 0


if __name__ == '__main__': raise SystemExit(main())
