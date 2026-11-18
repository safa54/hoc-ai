"""Ingest: turn expert-provided material (PDF, text, Markdown) into a Source with Chunks."""

from hoc.ingest.parsers import PARSERS, extract_text, register_parser
from hoc.ingest.segment import ingest_text, split_into_chunks

__all__ = ["PARSERS", "extract_text", "ingest_text", "register_parser", "split_into_chunks"]
