import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gmail2webhook.config import Account, Config, Route, load_config
from gmail2webhook.forwarder import State, run_once, send_webhook
from gmail2webhook.gmail import message_ids
from gmail2webhook.mail import body_text, payloads


def encoded(text, charset="utf-8"):
    return base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")


def mail(mid="m1", sender="Orders <orders@example.com>", text="hello"):
    return {"id": mid, "payload": {"mimeType": "text/plain", "headers": [
        {"name": "From", "value": sender}, {"name": "Subject", "value": "Order"}],
        "body": {"data": encoded(text)}}}


class ForwarderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = State(self.root / "state.sqlite3")
        self.addCleanup(lambda: self.state.close())
        self.route = Route("orders", ("orders@example.com",), "https://discord.com/api/webhooks/123/token")
        self.account = Account("a", "a@gmail.com", self.root / "client", self.root / "token", (self.route,))

    def test_pagination_includes_spam_trash(self):
        service = Mock()
        method = service.users.return_value.messages.return_value.list
        method.return_value.execute.side_effect = [{"messages": [{"id": "1"}], "nextPageToken": "next"}, {"messages": [{"id": "2"}]}]
        self.assertEqual(list(message_ids(service, self.route.senders)), ["1", "2"])
        self.assertTrue(method.call_args.kwargs["includeSpamTrash"])
        self.assertEqual(method.call_args.kwargs["pageToken"], "next")
        self.assertEqual(method.call_args.kwargs["q"], "{from:orders@example.com}")

    def test_multiple_accounts_routes_exact_sender_and_persistent_dedup(self):
        extra = Route("copy", self.route.senders, "https://discord.com/api/webhooks/456/token")
        first = Account("a", "a@gmail.com", self.root / "c", self.root / "t1", (self.route, extra))
        second = Account("b", "b@gmail.com", self.root / "c", self.root / "t2", (self.route,))
        config = Config(self.root / "state", 60, (first, second))
        services = {a.id: Mock() for a in config.accounts}
        for service in services.values():
            messages = service.users.return_value.messages.return_value
            messages.list.return_value.execute.return_value = {"messages": [{"id": "m1"}, {"id": "false-match"}]}
        # Bind each mock separately rather than closing over loop variables.
        for service in services.values():
            messages = service.users.return_value.messages.return_value
            messages.get.side_effect = lambda messages=messages, **kw: SimpleNamespace(execute=lambda **_: mail() if kw["id"] == "m1" else mail("false-match", "other@example.com"))
        session = Mock()
        session.post.return_value.status_code = 200
        self.assertEqual(run_once(config, self.state, session, lambda a: services[a.id]), 0)
        self.assertEqual(session.post.call_count, 3)
        self.state.close()
        self.state = State(self.root / "state.sqlite3")
        self.assertEqual(run_once(config, self.state, session, lambda a: services[a.id]), 0)
        self.assertEqual(session.post.call_count, 3)

    def test_partial_delivery_resumes_after_restart(self):
        key = ("a", "orders", "m1")
        sent = []
        def fail_second(payload):
            if payload == 2:
                raise RuntimeError("offline")
            sent.append(payload)
        with self.assertRaises(RuntimeError):
            self.state.deliver(key, [1, 2, 3], fail_second)
        self.assertFalse(self.state.done(key))
        self.state.deliver(key, [1, 2, 3], sent.append)
        self.assertEqual(sent, [1, 2, 3])
        self.assertTrue(self.state.done(key))

    def test_rate_limit_and_server_error_retry(self):
        session, sleep = Mock(), Mock()
        session.post.side_effect = [SimpleNamespace(status_code=429, json=lambda: {"retry_after": 1.5}), SimpleNamespace(status_code=503), SimpleNamespace(status_code=200)]
        send_webhook(self.route.webhook, {}, session, sleep)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1.5, 2])
        self.assertEqual(session.post.call_args.kwargs["params"], {"wait": "true"})

    def test_permanent_webhook_failure_not_recorded(self):
        session = Mock()
        session.post.return_value.status_code = 404
        with self.assertRaises(RuntimeError):
            self.state.deliver(("a", "r", "m"), [{}], lambda p: send_webhook(self.route.webhook, p, session))
        self.assertFalse(self.state.done(("a", "r", "m")))

    def test_account_failure_does_not_stop_other_accounts(self):
        second = Account("b", "b@gmail.com", self.root / "c", self.root / "t", (self.route,))
        service = Mock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {"messages": [{"id": "m1"}]}
        service.users.return_value.messages.return_value.get.return_value.execute.return_value = mail()
        def factory(account):
            if account.id == "a":
                raise RuntimeError("expired token")
            return service
        session = Mock()
        session.post.return_value.status_code = 200
        self.assertEqual(run_once(Config(self.root / "state", 60, (self.account, second)), self.state, session, factory), 1)
        self.assertTrue(self.state.done(("b", "orders", "m1")))

    def test_mime_html_charset_and_attachment_backed_body(self):
        message = {"payload": {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": "text/plain; charset=big5"}], "body": {"attachmentId": "body"}},
            {"mimeType": "text/html", "body": {"data": encoded("<p>fallback</p>")}}]}}
        self.assertEqual(body_text(message, lambda _: encoded("中文", "big5")), "中文")
        message["payload"] = {"mimeType": "text/html", "body": {"data": encoded("<style>hidden</style><p>Hello &amp; world</p><script>hidden</script>")}}
        self.assertEqual(body_text(message, Mock()), "Hello & world")

    def test_long_unicode_mail_embed_limits_and_no_mentions(self):
        text = "😀中文" * 3000 + "@everyone **bold**"
        result = list(payloads(self.account, mail(), text))
        self.assertGreater(len(result), 1)
        combined = "".join(p["embeds"][0]["description"] for p in result)
        self.assertIn("😀中文" * 3000, combined)
        self.assertIn("@\u200beveryone", combined)
        for p in result:
            e = p["embeds"][0]
            size = lambda s: len(s.encode("utf-16-le")) // 2
            self.assertLessEqual(size(e["description"]), 4096)
            self.assertLessEqual(size(e["title"]), 256)
            total = size(e["title"]) + size(e["description"]) + size(e["footer"]["text"]) + sum(size(f["name"]) + size(f["value"]) for f in e.get("fields", []))
            self.assertLessEqual(total, 6000)
            self.assertEqual(p["allowed_mentions"], {"parse": []})

    def test_configuration_env_and_duplicate_tokens(self):
        account = {"id": "a", "email": "a@gmail.com", "credentials_file": "client.json", "token_file": "token.json", "routes": [{"id": "r", "senders": ["ORDERS@example.com"], "webhook_env": "TEST_WEBHOOK"}]}
        path = self.root / "config.json"
        path.write_text(json.dumps({"accounts": [account]}))
        with patch.dict(os.environ, {"TEST_WEBHOOK": self.route.webhook}):
            config = load_config(path)
            self.assertEqual(config.accounts[0].routes[0].senders, self.route.senders)
            self.assertEqual(config.accounts[0].token_file, self.root / "token.json")
            path.write_text(json.dumps({"accounts": [account, dict(account, id="b")]}))
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
