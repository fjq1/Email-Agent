"""仅提取纯文本与附件元信息，不渲染邮件 HTML，也不加载远程图片。"""

import base64
import binascii
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from django.conf import settings
from django.utils.dateparse import parse_datetime


class PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        elif not self.hidden and tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_to_text(value):
    parser = PlainTextParser()
    parser.feed(value)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def decoded_header(value):
    try:
        return str(make_header(decode_header(value or "")))
    except (LookupError, UnicodeError):
        return value or ""


def addresses(value):
    return [
        {"name": decoded_header(name), "address": address}
        for name, address in getaddresses([value or ""])
        if address
    ]


def message_date(value):
    try:
        result = parse_datetime(value) or parsedate_to_datetime(value)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def milliseconds_date(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def bounded_text(value):
    limit = settings.MAIL_MAX_BODY_CHARS
    return value[:limit], len(value) > limit


def gmail_body(payload):
    plain, html, attachments = [], [], []
    # 使用栈遍历 multipart，避免深层嵌套邮件触发 Python 递归上限。
    pending = [payload]
    while pending:
        part = pending.pop()
        headers = {item["name"].lower(): item.get("value", "") for item in part.get("headers", [])}
        body = part.get("body", {})
        filename = decoded_header(part.get("filename", ""))
        if filename or headers.get("content-disposition", "").lower().startswith("attachment"):
            attachments.append(
                {
                    "id": body.get("attachmentId", ""),
                    "name": filename or "未命名附件",
                    "content_type": part.get("mimeType", ""),
                    "size": body.get("size", 0),
                }
            )
            continue
        pending.extend(reversed(part.get("parts", [])))
        data = body.get("data", "")
        if not data or part.get("mimeType") not in {"text/plain", "text/html"}:
            continue
        content_type = Message()
        content_type["content-type"] = headers.get("content-type", "text/plain; charset=utf-8")
        try:
            raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            try:
                text = raw.decode(content_type.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
        except (ValueError, binascii.Error):
            continue
        (plain if part["mimeType"] == "text/plain" else html).append(text)
    text = "\n".join(plain) if plain else html_to_text("\n".join(html))
    text, truncated = bounded_text(text)
    return text, truncated, attachments
