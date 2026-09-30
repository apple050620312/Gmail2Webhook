import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Route:
    id: str
    senders: tuple[str, ...]
    webhook: str


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    credentials_file: Path
    token_file: Path
    routes: tuple[Route, ...]


@dataclass(frozen=True)
class Config:
    state_db: Path
    interval: int
    accounts: tuple[Account, ...]


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent
    interval = raw.get("poll_interval_seconds", 60)
    if type(interval) is not int or interval < 1:
        raise ValueError("poll_interval_seconds must be a positive integer")
    accounts = []
    ids, tokens = set(), set()
    for item in raw["accounts"]:
        account_id = item["id"]
        if not isinstance(account_id, str) or not account_id or account_id in ids:
            raise ValueError("Account IDs must be nonempty and unique")
        ids.add(account_id)
        email = item["email"].strip().lower()
        if not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+', email):
            raise ValueError("Invalid account email")
        token = (base / item["token_file"]).resolve()
        if token in tokens:
            raise ValueError("Each account needs its own token_file")
        tokens.add(token)
        routes, route_ids = [], set()
        for r in item["routes"]:
            if not isinstance(r["id"], str) or not r["id"] or r["id"] in route_ids:
                raise ValueError("Route IDs must be nonempty and unique within an account")
            route_ids.add(r["id"])
            senders = tuple(dict.fromkeys(s.strip().lower() for s in r["senders"]))
            if not senders or any(not re.fullmatch(r'[^\s<>@(){}"\\]+@[^\s<>@(){}"\\]+', s) for s in senders):
                raise ValueError("senders must contain exact email addresses")
            if bool(r.get("webhook_env")) == bool(r.get("webhook_url")):
                raise ValueError("Set exactly one of webhook_env or webhook_url")
            webhook = os.environ.get(r["webhook_env"], "") if r.get("webhook_env") else r["webhook_url"]
            url = urlsplit(webhook)
            if url.scheme != "https" or url.netloc not in ("discord.com", "discordapp.com") or not re.fullmatch(r"/api/webhooks/[0-9]+/[A-Za-z0-9_-]+", url.path) or url.fragment:
                raise ValueError("Missing or invalid Discord webhook URL")
            routes.append(Route(r["id"], senders, webhook))
        if not routes:
            raise ValueError("Each account needs at least one route")
        accounts.append(Account(account_id, email, base / item["credentials_file"], token, tuple(routes)))
    if not accounts:
        raise ValueError("At least one account is required")
    return Config(base / raw.get("state_db", "data/state.sqlite3"), interval, tuple(accounts))
