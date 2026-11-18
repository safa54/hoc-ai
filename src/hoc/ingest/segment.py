"""Segmentation: paragraphs grouped into chunks of roughly ``chunk_chars`` characters."""

from __future__ import annotations

import hashlib
import re

from hoc.core.provenance import Chunk, Source, SourceType

_BREAK = re.compile(r"\n\s*\n")


def split_into_chunks(text: str, chunk_chars: int = 1800) -> list[str]:
    chunks: list[str] = []
    current = ""
    for para in (p.strip() for p in _BREAK.split(text)):
        if not para:
            continue
        while len(para) > chunk_chars * 2:  # a single huge paragraph: hard split
            chunks.append(para[:chunk_chars])
            para = para[chunk_chars:]
        if current and len(current) + len(para) > chunk_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def ingest_text(
    title: str,
    text: str,
    *,
    type_: SourceType = SourceType.NOTES,
    path: str | None = None,
    chunk_chars: int = 1800,
) -> tuple[Source, list[Chunk]]:
    source = Source(
        title=title,
        type=type_,
        file_hash=hashlib.sha256(text.encode()).hexdigest(),
        path=path,
    )
    chunks = [
        Chunk(source_id=source.id, locator=f"{title} #{i + 1}", text=part, ordinal=i)
        for i, part in enumerate(split_into_chunks(text, chunk_chars))
    ]
    return source, chunks
