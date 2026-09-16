"""Markdown document parser."""
from __future__ import annotations

import re
from pathlib import Path

from .base import BaseParser, ParsedDocument


class MarkdownParser(BaseParser):
    def parse(self, file_path: str) -> ParsedDocument:
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        sections = self._extract_sections(content)
        metadata = self.extract_metadata(file_path)
        m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if m:
            metadata["title"] = m.group(1).strip()
        metadata["word_count"] = len(content.split())
        return ParsedDocument(content=content, sections=sections, metadata=metadata)

    def _extract_sections(self, content: str) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        current_title = ""
        current_lines: list[str] = []

        for line in content.splitlines():
            m = re.match(r"^(#{1,4})\s+(.+)$", line)
            if m:
                if current_lines:
                    sections.append({
                        "title": current_title,
                        "content": "\n".join(current_lines).strip(),
                    })
                current_title = m.group(2).strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_lines:
            sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})

        return sections
