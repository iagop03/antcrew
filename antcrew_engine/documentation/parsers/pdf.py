"""PDF parser. Requires pypdf (or PyPDF2 as fallback)."""
from __future__ import annotations

from .base import BaseParser, ParsedDocument


class PDFParser(BaseParser):
    def parse(self, file_path: str) -> ParsedDocument:
        try:
            from pypdf import PdfReader
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore[no-redef]
            except ImportError:
                raise ImportError("pypdf is required for .pdf files: pip install pypdf")

        reader = PdfReader(file_path)
        pages: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text.strip())

        content = "\n\n".join(p for p in pages if p)
        sections = [
            {"title": f"Page {i + 1}", "content": p}
            for i, p in enumerate(pages)
            if p
        ]

        metadata = self.extract_metadata(file_path)
        metadata["page_count"] = len(reader.pages)
        metadata["word_count"] = len(content.split())

        return ParsedDocument(content=content, sections=sections, metadata=metadata)
