import base64
import html
import re
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit


def clean_whitespace(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def escape_markdown(text):
    return re.sub(r'([\\`*_~|>\[\]])', r'\\\1', text).replace("@", "@\u200b")


def link_url(url):
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() in ("http", "https") and parsed.hostname:
            return quote(url.strip(), safe=":/?#@!$&'+,;=%-._~")
    except ValueError:
        pass
    return None


def markdown_text(text):
    """Escape mail formatting while keeping ordinary web URLs clickable."""
    result, position = [], 0
    for match in re.finditer(r"https?://[^\s<>]+", text, re.I):
        raw = match.group().rstrip(".,;!?:\"'，。；！")
        while raw.endswith(")") and raw.count(")") > raw.count("("):
            raw = raw[:-1]
        raw = raw.rstrip("]}")
        url = link_url(raw)
        result.append(escape_markdown(text[position:match.start()]))
        result.append(f"<{url}>" if url else escape_markdown(raw))
        result.append(escape_markdown(match.group()[len(raw):]))
        position = match.end()
    result.append(escape_markdown(text[position:]))
    return "".join(result)


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.hidden = 0
        self.anchor = None

    def finish_anchor(self):
        if self.anchor is not None:
            href, label = self.anchor
            label = re.sub(r"\s+", " ", "".join(label)).strip()
            url = link_url(href)
            if url:
                self.text.append(f"[{escape_markdown(label or href)}](<{url}>)")
            else:
                self.text.append(escape_markdown(label))
            self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if self.hidden:
            return
        if tag == "a":
            self.finish_anchor()
            self.anchor = (dict(attrs).get("href") or "", [])
        if tag == "img":
            self.handle_data(dict(attrs).get("alt") or "")
        if tag in ("br", "p", "div", "li", "tr") and not self.hidden:
            if self.anchor is not None:
                self.anchor[1].append(" ")
            else:
                self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if tag == "a" and not self.hidden:
            self.finish_anchor()
        if tag in ("p", "div", "li", "tr") and not self.hidden:
            if self.anchor is not None:
                self.anchor[1].append(" ")
            else:
                self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            # HTML source indentation is whitespace, not a paragraph break.
            data = re.sub(r"\s+", " ", data)
            if self.anchor is not None:
                self.anchor[1].append(data)
            else:
                self.text.append(markdown_text(data))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ("br", "hr", "img", "input", "meta", "link"):
            self.handle_endtag(tag)


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
                rich = [t for p, t in zip(children, texts) if p.get("mimeType") == "text/html" and t.strip()]
                return rich[0] if rich else next((t for t in texts if t.strip()), "")
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
            parser.close()
            parser.finish_anchor()
            text = "".join(parser.text)
        else:
            text = markdown_text(text)
        return clean_whitespace(text)
    return clean_whitespace(extract(message.get("payload", {}))) or markdown_text(clean_whitespace(html.unescape(message.get("snippet", "")))) or "（無文字內容）"


def chunks(text, limit, keep_links=False):
    result, current, size = [], [], 0
    # Keep generated Markdown links together when they fit within one embed.
    pattern = r'(\[(?:\\.|[^\]\\\n])*\]\(<https?://[^>\n]+>\)|<https?://[^>\n]+>)'
    segments = re.split(pattern, text) if keep_links else [text]
    tokens = (token for i, segment in enumerate(segments)
              for token in ([segment] if keep_links and i % 2 and len(segment.encode("utf-16-le")) // 2 <= limit else segment))
    for char in tokens:
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
    # body_text already escapes mail text and renders trusted Markdown links.
    parts = chunks(clean_whitespace(text), 4000, keep_links=True)
    title = chunks(escape_markdown(h.get("subject") or "（無主旨）"), 200)[0]
    metadata = [("寄件人", h.get("from", "（未知）")), ("收件人", h.get("to", "（未知）"))]
    date = chunks(clean_whitespace(h.get("date") or "（未知）"), 250)[0]
    for index, part in enumerate(parts, 1):
        embed = {"title": f"{title} ({index}/{len(parts)})", "description": part, "color": 0x4285F4,
                 "footer": {"text": f"日期：{date}"}}
        if index == 1:
            embed["fields"] = [{"name": name, "value": chunks(escape_markdown(value), 250)[0], "inline": False} for name, value in metadata]
        yield {"embeds": [embed], "allowed_mentions": {"parse": []}}
