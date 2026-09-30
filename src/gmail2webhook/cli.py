import argparse
import logging
import time
from pathlib import Path

from .config import load_config
from .forwarder import State, run_once
from .gmail import service_for


def main(argv=None):
    parser = argparse.ArgumentParser(description="Forward Gmail mail to Discord embeds")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("authorize", help="Authorize configured Gmail accounts")
    auth.add_argument("--account", help="Authorize just this account ID")
    run = sub.add_parser("run", help="Poll and forward all matching mail")
    run.add_argument("--once", action="store_true", help="Run one synchronization then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        if args.command == "authorize":
            accounts = [a for a in config.accounts if not args.account or a.id == args.account]
            if not accounts:
                raise ValueError("Unknown account ID")
            for account in accounts:
                service_for(account, authorize=True)
                logging.info("Authorized account=%s", account.id)
            return 0
        import requests
        state = State(config.state_db)
        try:
            with requests.Session() as session:
                while True:
                    failures = run_once(config, state, session)
                    if args.once:
                        return 1 if failures else 0
                    time.sleep(config.interval)
        finally:
            state.close()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # HTTP exception strings may contain OAuth or webhook secrets.
        logging.error("Command failed (%s). Check configuration, tokens and connectivity.", type(exc).__name__)
        return 1
