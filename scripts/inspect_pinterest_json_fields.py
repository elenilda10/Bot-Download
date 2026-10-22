#!/usr/bin/env python3
"""Show gallery-dl -J Pinterest metadata fields, without exposing private tokens.

Uses the real gallery-dl protocol (Directory=2, Url=3, Queue=6).
Reads JSON already saved locally; does not make network requests or download.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

CAPTION_KEYS = (
    "title", "seo_title", "description", "seo_description",
    "closeup_description", "closeup_unified_description",
    "alt_text", "seo_alt_text", "auto_alt_text",
)
MEDIA_KEYS = (
    "id", "extension", "num", "width", "height",
    "media_id", "page_id", "count",
)
REDACT_KEYS = ("cookie", "token", "secret", "key", "password", "session",
               "invite", "auth", "hash", "code", "access")


def _format(value):
    if isinstance(value, str):
        return repr(value[:500])
    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)
    if isinstance(value, dict):
        return "{objeto com " + str(len(value)) + " campos}"
    if isinstance(value, list):
        return "[lista com " + str(len(value)) + " itens]"
    return type(value).__name__


def _records(data):
    if isinstance(data, list) and data and isinstance(data[0], int):
        return [data]
    if isinstance(data, list):
        return data
    return [data]


def main():
    name = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/pinterest_resolved.json")
    data = json.loads(name.read_text(encoding="utf-8"))
    records = _records(data)
    print(f"Arquivo: {name} | Registros: {len(records)}")
    for i, record in enumerate(records[:30], 1):
        if not isinstance(record, list) or not record:
            continue
        kind = record[0]
        info = {2: "METADADOS DO PIN", 3: "ARQUIVO DE MÍDIA", 6: "REDIRECIONAMENTO"}
        print(f"\nREGISTRO {i} | {info.get(kind, 'Outro')} | código {kind}")
        if kind == 2 and len(record) >= 2 and isinstance(record[1], dict):
            meta = record[1]
            print("CAMPOS DISPONÍVEIS:")
            print(", ".join(sorted(meta)) or "(nenhum)")
            print("VALORES ÚTEIS:")
            if meta.get("id") is not None:
                print("  id:", _format(meta["id"]))
            for key in CAPTION_KEYS:
                if key in meta:
                    print(f"  {key}: {_format(meta[key])}")
            for key in ("pinner", "images", "videos", "carousel_data", "story_pin_data"):
                value = meta.get(key)
                if isinstance(value, dict):
                    public_keys = [k for k in value
                                   if not any(secret in k.lower() for secret in REDACT_KEYS)]
                    print(f"  {key}: objeto com {len(value)} campos; subcampos:", ", ".join(public_keys[:40]))
                elif isinstance(value, list):
                    print(f"  {key}: lista com {len(value)} itens")
        elif kind == 3 and len(record) >= 3:
            media_url = record[1]
            if isinstance(media_url, str):
                parts = urlsplit(media_url)
                print("URL (sem parâmetros):", parts.scheme + "://" + parts.netloc + parts.path[:180])
            media = record[2] if isinstance(record[2], dict) else {}
            print("CAMPOS DA MÍDIA:", ", ".join(sorted(media)) or "(nenhum)")
            for key in MEDIA_KEYS + CAPTION_KEYS:
                if key in media:
                    print(f"  {key}: {_format(media[key])}")
        elif kind == 6 and len(record) >= 2 and isinstance(record[1], str):
            parts = urlsplit(record[1])
            print("Destino (sem parâmetros):", parts.scheme + "://" + parts.netloc + parts.path[:180])


if __name__ == "__main__":
    main()
