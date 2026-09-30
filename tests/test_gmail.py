import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from google.auth.credentials import AnonymousCredentials
from googleapiclient.discovery import build

from gmail2webhook.config import Account
from gmail2webhook.gmail import SCOPES, service_for


class GmailTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.account = Account("a", "a@gmail.com", self.root / "client.json", self.root / "token.json", ())

    def test_missing_token_requires_explicit_authorization(self):
        with self.assertRaisesRegex(RuntimeError, "authorize"):
            service_for(self.account)

    def test_refresh_and_profile_verification(self):
        self.account.token_file.write_text("{}")
        credentials = Mock(expired=True, refresh_token="refresh", valid=True)
        credentials.to_json.return_value = '{"token":"renewed"}'
        service = Mock()
        service.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": "a@gmail.com"}
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=credentials) as load, patch("googleapiclient.discovery.build", return_value=service):
            self.assertIs(service_for(self.account), service)
            load.assert_called_once_with(str(self.account.token_file), SCOPES)
        credentials.refresh.assert_called_once()
        self.assertEqual(self.account.token_file.read_text(), '{"token":"renewed"}')

    def test_wrong_authorized_account_is_not_saved(self):
        credentials = Mock(expired=False, valid=True)
        flow = Mock()
        flow.run_local_server.return_value = credentials
        service = Mock()
        service.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": "wrong@gmail.com"}
        with patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file", return_value=flow), patch("googleapiclient.discovery.build", return_value=service):
            with self.assertRaisesRegex(ValueError, "does not match"):
                service_for(self.account, authorize=True)
        self.assertFalse(self.account.token_file.exists())
        self.assertEqual(flow.run_local_server.call_args.kwargs["login_hint"], "a@gmail.com")

    def test_real_discovery_supports_used_gmail_api_arguments(self):
        # Uses the library's bundled discovery document, without network calls.
        service = build("gmail", "v1", credentials=AnonymousCredentials(), static_discovery=True)
        messages = service.users().messages()
        request = messages.list(userId="me", q="{from:orders@example.com}", includeSpamTrash=True, maxResults=500, pageToken="next")
        self.assertIn("includeSpamTrash=true", request.uri)
        self.assertIn("pageToken=next", request.uri)
        self.assertIn("format=full", messages.get(userId="me", id="m1", format="full").uri)
        self.assertIn("/attachments/body", messages.attachments().get(userId="me", messageId="m1", id="body").uri)


if __name__ == "__main__":
    unittest.main()
