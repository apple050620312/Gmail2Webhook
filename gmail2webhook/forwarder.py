import logging
import sqlite3
import time

from .gmail import message_ids, service_for
from .mail import body_text, payloads, sender

log = logging.getLogger(__name__)


def send_webhook(url, payload, session, sleep=time.sleep):
    for attempt in range(6):
        # wait=true makes Discord acknowledge creation of the message.
        response = session.post(url, params={"wait": "true"}, json=payload, timeout=(10, 60), allow_redirects=False)
        if 200 <= response.status_code < 300:
            return
        if response.status_code == 429:
            try:
                delay = float(response.json()["retry_after"])
            except (ValueError, KeyError, TypeError):
                delay = 2 ** attempt
            sleep(max(0.1, min(delay, 300)))
        elif response.status_code >= 500:
            sleep(2 ** attempt)
        else:
            raise RuntimeError(f"Discord HTTP {response.status_code}")
    raise RuntimeError("Discord retry limit exceeded")


class State:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=120)
        self.db.execute("CREATE TABLE IF NOT EXISTS deliveries (account TEXT, route TEXT, message TEXT, next_part INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account, route, message))")
        self.db.commit()

    def done(self, key):
        row = self.db.execute("SELECT done FROM deliveries WHERE account=? AND route=? AND message=?", key).fetchone()
        return bool(row and row[0])

    def deliver(self, key, parts, send):
        for index, payload in enumerate(parts):
            # Hold a write lock during delivery to serialize concurrent workers.
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("INSERT OR IGNORE INTO deliveries(account,route,message) VALUES(?,?,?)", key)
                next_part, done = self.db.execute("SELECT next_part,done FROM deliveries WHERE account=? AND route=? AND message=?", key).fetchone()
                if done:
                    return
                if index < next_part:
                    continue
                send(payload)
                self.db.execute("UPDATE deliveries SET next_part=? WHERE account=? AND route=? AND message=?", (index + 1, *key))
        with self.db:
            self.db.execute("UPDATE deliveries SET done=1 WHERE account=? AND route=? AND message=?", key)

    def close(self):
        self.db.close()


def run_once(config, state, session, service_factory=service_for):
    failures = 0
    for account in config.accounts:
        try:
            service = service_factory(account)
            senders = tuple(dict.fromkeys(s for route in account.routes for s in route.senders))
            queued = []
            for message_id in message_ids(service, senders):
                pending = [r for r in account.routes if not state.done((account.id, r.id, message_id))]
                if not pending:
                    continue
                try:
                    message = service.users().messages().get(userId="me", id=message_id, format="full").execute(num_retries=3)
                    matching = [r for r in pending if sender(message) in r.senders]
                    if not matching:
                        continue
                    queued.append((int(message["internalDate"]), message_id, message, matching))
                except Exception as exc:
                    failures += 1
                    log.error("Message failed account=%s message=%s error=%s", account.id, message_id, type(exc).__name__)
            # Gmail search order is newest first; collect every page before sending.
            for _, message_id, message, matching in sorted(queued, key=lambda item: (item[0], item[1])):
                try:
                    def load_attachment(attachment_id):
                        return service.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute(num_retries=3)["data"]
                    parts = list(payloads(account, message, body_text(message, load_attachment)))
                    for route in matching:
                        try:
                            state.deliver((account.id, route.id, message_id), parts, lambda p: send_webhook(route.webhook, p, session))
                            log.info("Delivered account=%s route=%s message=%s", account.id, route.id, message_id)
                        except Exception as exc:
                            failures += 1
                            log.error("Delivery failed account=%s route=%s message=%s error=%s", account.id, route.id, message_id, type(exc).__name__)
                except Exception as exc:
                    failures += 1
                    log.error("Message failed account=%s message=%s error=%s", account.id, message_id, type(exc).__name__)
        except Exception as exc:
            failures += 1
            log.error("Account failed account=%s error=%s", account.id, type(exc).__name__)
    return failures
