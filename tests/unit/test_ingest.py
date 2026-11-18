import pytest

from hoc.core.errors import HocError
from hoc.ingest import extract_text, ingest_text, split_into_chunks


def test_chunks_group_paragraphs_and_split_giants():
    text = "\n\n".join(f"para {i} " + "x" * 300 for i in range(10))
    chunks = split_into_chunks(text, 1000)
    assert len(chunks) >= 3 and all(len(c) <= 1400 for c in chunks)
    assert "".join(split_into_chunks("y" * 5000, 1000)) == "y" * 5000


def test_ingest_text_and_parsers():
    source, chunks = ingest_text("Notes", "alpha\n\nbeta")
    assert source.title == "Notes" and [c.ordinal for c in chunks] == [0]
    assert extract_text("a.md", b"# hi") == "# hi"
    with pytest.raises(HocError, match="unsupported"):
        extract_text("a.docx", b"x")


def test_pdf_without_text_is_a_clear_error():
    import io

    from pypdf import PdfWriter

    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.write(buf)
    with pytest.raises(HocError, match="no extractable text"):
        extract_text("scan.pdf", buf.getvalue())
