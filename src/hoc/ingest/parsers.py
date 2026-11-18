"""File parsers, selected by suffix. Register another parser to support a new format or to
swap the PDF engine (e.g. a layout- or LaTeX-aware one) without touching callers."""

from __future__ import annotations

import io
from collections.abc import Callable

from hoc.core.errors import HocError

Parser = Callable[[bytes], str]
PARSERS: dict[str, Parser] = {}


def register_parser(*suffixes: str) -> Callable[[Parser], Parser]:
    def decorator(fn: Parser) -> Parser:
        for suffix in suffixes:
            PARSERS[suffix.lower()] = fn
        return fn

    return decorator


@register_parser(".txt", ".md", ".markdown", ".tex", "")
def parse_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


@register_parser(".pdf")
def parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except PyPdfError as e:
        raise HocError(f"could not read the PDF: {e}") from e
    text = "\n\n".join(f"[page {i}]\n{t.strip()}" for i, t in enumerate(pages, 1) if t.strip())
    if not text:
        raise HocError("the PDF contains no extractable text (scanned PDFs need OCR first)")
    return text


def extract_text(filename: str, data: bytes) -> str:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    parser = PARSERS.get(suffix)
    if parser is None:
        raise HocError(f"unsupported file type {suffix!r}; supported: {sorted(PARSERS)}")
    return parser(data)
