"""Pterodactyl entry point: python -u main.py."""
import sys

from gmail2webhook.cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["run"]))
