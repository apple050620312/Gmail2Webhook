import base64
import html
import re
from email.utils import parseaddr
from html.parser import HTMLParser


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "li", "tr") and not self.hidden:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if tag in ("p", "div", "li", "tr") and not self.hidden:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def headers(message):
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def sender(message):
    return parseaddr(headers(message).get("from", ""))[1].lower()


def body_text(message, attachment_loader):
    def extract(part):
        if part.get("filename") or any(h["name"].lower() == "content-disposition" and h["value"].lower().startswith("attachment") for h in part.get("headers", [])):
            return ""
        children = part.get("parts", [])
        if children:
            texts = [extract(p) for p in children]
            if part.get("mimeType") == "multipart/alternative":
                plain = [t for p, t in zip(children, texts) if p.get("mimeType") == "text/plain" and t.strip()]
                return plain[0] if plain else next((t for t in reversed(texts) if t.strip()), "")
            return "\n".join(t for t in texts if t)
        mime = part.get("mimeType", "")
        if mime not in ("text/plain", "text/html"):
            return ""
        body = part.get("body", {})
        data = body.get("data")
        if data is None and body.get("attachmentId"):
            data = attachment_loader(body["attachmentId"])
        if not data:
            return ""
        content_type = next((h["value"] for h in part.get("headers", []) if h["name"].lower() == "content-type"), "")
        match = re.search(r'charset\s*=\s*["\']?([^\s;"\']+)', content_type, re.I)
        charset = match.group(1) if match else "utf-8"
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        try:
            text = decoded.decode(charset, errors="replace")
        except LookupError:
            text = decoded.decode("utf-8", errors="replace")
        if mime == "text/html":
            parser = TextHTML()
            parser.feed(text)
            text = "".join(parser.text)
        return text.strip()
    return extract(message.get("payload", {})) or html.unescape(message.get("snippet", "")) or "（無文字內容）"


def chunks(text, limit):
    result, current, size = [], [], 0
    for char in text:
        units = len(char.encode("utf-16-le")) // 2
        if size + units > limit:
            result.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += units
    if current:
        result.append("".join(current))
    return result or [""]


def payloads(account, message, text):
    h = headers(message)
    # Escape Discord markdown and prevent mail text from creating mentions.
    def safe(value):
        return re.sub(r'([\\`*_~|>])', r'\\\1', value).replace("@", "@\u200b")
    parts = chunks(safe(text), 4000)
    title = chunks(safe(h.get("subject") or "（無主旨）"), 200)[0]
    metadata = [("Gmail 帳號", account.email), ("寄件人", h.get("from", "（未知）")), ("收件人", h.get("to", "（未知）")), ("日期", h.get("date", "（未知）"))]
    for index, part in enumerate(parts, 1):
        embed = {"title": f"{title} ({index}/{len(parts)})", "description": part, "color": 0x4285F4,
                 "footer": {"text": f"Gmail ID: {message['id']}"}}
        if index == 1:
            embed["fields"] = [{"name": name, "value": chunks(safe(value), 250)[0], "inline": False} for name, value in metadata]
        yield {"embeds": [embed], "allowed_mentions": {"parse": []}}
