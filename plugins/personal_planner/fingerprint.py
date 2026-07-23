"""Stable fingerprints for notification deduplication."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_content(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _WHITESPACE_RE.sub(" ", normalized).strip().casefold()


def normalize_source_uri(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            path,
            parts.query,
            "",  # fragments do not identify a different notification
        )
    )


def content_hash(value: str) -> str:
    return hashlib.sha256(normalize_content(value).encode("utf-8")).hexdigest()


def source_key(*, source_type: str, source_uri: str | None, raw_content: str) -> str:
    normalized_uri = normalize_source_uri(source_uri)
    if normalized_uri:
        identity = f"uri\0{normalized_uri}"
    else:
        identity = f"content\0{content_hash(raw_content)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
