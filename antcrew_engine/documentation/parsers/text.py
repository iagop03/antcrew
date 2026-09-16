"""Plain-text document parser."""
from __future__ import annotations

from pathlib import Path

from .base import BaseParser, ParsedDocument


class TextParser(BaseParser):
    def parse(self, file_path: str) -> ParsedDocument:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        metadata = self.extract_metadata(file_path)
        metadata["word_count"] = len(content.split())
        return ParsedDocument(
            content=content,
            sections=[{"title": "Content", "content": content}],
            metadata=metadata,
        )
