import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def save_token(path: Path, credentials):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    os.replace(temporary, path)


def service_for(account, authorize=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if account.token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(account.token_file), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not authorize:
            raise RuntimeError("Run authorize for this account first")
        flow = InstalledAppFlow.from_client_secrets_file(str(account.credentials_file), SCOPES)
        credentials = flow.run_local_server(port=0, prompt="consent", login_hint=account.email)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    actual = service.users().getProfile(userId="me").execute(num_retries=3)["emailAddress"]
    if actual.lower() != account.email.lower():
        raise ValueError("Authorized Gmail account does not match configured email")
    save_token(account.token_file, credentials)
    return service


def message_ids(service, senders):
    query = "{" + " ".join(f"from:{s}" for s in senders) + "}"
    page = None
    while True:
        response = service.users().messages().list(userId="me", q=query, includeSpamTrash=True, maxResults=500, pageToken=page).execute(num_retries=3)
        for message in response.get("messages", []):
            yield message["id"]
        page = response.get("nextPageToken")
        if not page:
            break
