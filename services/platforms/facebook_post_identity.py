"""Read photo attachments only from JSON objects bound to the requested post.

No network, cookies, Telegram imports, or generic first-photo/text fallback.
Unknown, conflicting, mixed-media and paginated structures fail closed.
"""
import json
from html.parser import HTMLParser

MAX_HTML = 16_000_000


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("type", "").lower() == "application/json"
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            try:
                self.documents.append(json.loads("".join(self.parts)))
            except (ValueError, RecursionError):
                pass
            self.active = False
            self.parts = []


def _walk(value):
    stack = [value]
    budget = 250_000
    while stack and budget:
        budget -= 1
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))
    if stack:
        raise ValueError("JSON traversal limit")


def _photos(attachments):
    ids = []
    # Only attachment structure edges; never comments, feedback or shared stories.
    edges = {"media", "styles", "attachment", "subattachments",
             "all_subattachments", "nodes", "edges", "node"}
    stack = [attachments]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
        elif isinstance(node, dict):
            if node.get("page_info", {}).get("has_next_page"):
                raise ValueError("Incomplete attachment connection")
            kind = node.get("__typename")
            if kind in {"Video", "VideoAttachment", "ExternalUrl"}:
                raise ValueError("Unsupported attachment")
            if kind == "Photo":
                identity = str(node.get("id", ""))
                if not identity.isdigit():
                    raise ValueError("Missing photo ID")
                if identity not in ids:
                    ids.append(identity)
            if "media" in node:
                media = node["media"]
                if not isinstance(media, dict) or media.get("__typename") != "Photo":
                    raise ValueError("Unsupported media")
            if node.get("attached_story") or node.get("shared_story"):
                raise ValueError("Shared story needs explicit support")
            stack.extend(reversed([v for k, v in node.items() if k in edges]))
    return tuple(ids)


def _message(story, post_id):
    texts = set()
    # Facebook repeats a Story in renderer sections. Only explicit story/message
    # edges are followed, and nested stories must carry the same post_id.
    msg = story.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("text"), str):
        texts.add(msg["text"].strip())
    sections = story.get("comet_sections")
    if isinstance(sections, dict):
        for name in ("content", "message"):
            section = sections.get(name)
            nested = section.get("story") if isinstance(section, dict) else None
            if isinstance(nested, dict) and str(nested.get("post_id")) == post_id:
                msg = nested.get("message")
                if isinstance(msg, dict) and isinstance(msg.get("text"), str):
                    texts.add(msg["text"].strip())
    if len(texts) > 1:
        raise ValueError("Conflicting post text")
    return next(iter(texts), "")


def extract_post_identity(html, post_id):
    """Return ordered IDs/text, or None when the requested binding is unproven."""
    if not str(post_id).isdigit() or len(html) > MAX_HTML:
        return None
    parser = _Scripts()
    try:
        parser.feed(html)
        records = []
        texts = set()
        for document in parser.documents:
            for node in _walk(document):
                if str(node.get("post_id")) != str(post_id):
                    continue
                text = _message(node, str(post_id))
                if text:
                    texts.add(text)
                if "attachments" not in node:
                    continue
                if not isinstance(node["attachments"], list):
                    return None
                ids = _photos(node["attachments"])
                if ids:
                    if not 1 <= len(ids) <= 10:
                        return None
                    records.append(ids)
        if not records or len(set(records)) != 1 or len(texts) > 1:
            return None
        return {"ids": records[0], "description": next(iter(texts), ""),
                "source_post_id": str(post_id)}
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None
