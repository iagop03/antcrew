"""Jira ticket parser — handles JSON exports and plain-text dumps."""
from __future__ import annotations

import json
from pathlib import Path

from .base import BaseParser, ParsedDocument


class JiraTicketParser(BaseParser):
    def parse(self, file_path: str) -> ParsedDocument:
        raw = Path(file_path).read_text(encoding="utf-8", errors="replace")
        metadata = self.extract_metadata(file_path)
        try:
            data = json.loads(raw)
            return self._from_json(data, metadata)
        except json.JSONDecodeError:
            return self._from_text(raw, metadata)

    def _from_json(self, data: dict, metadata: dict) -> ParsedDocument:
        fields = data.get("fields", data)
        key = data.get("key", "")
        summary = fields.get("summary", "")
        description = fields.get("description", "")
        if isinstance(description, dict):
            description = self._adf_to_text(description)

        content = f"# {key}: {summary}\n\n{description or ''}"
        metadata.update({"key": key, "summary": summary, "word_count": len(content.split())})

        return ParsedDocument(
            content=content,
            sections=[
                {"title": "Summary", "content": summary},
                {"title": "Description", "content": str(description)},
            ],
            metadata=metadata,
        )

    def _from_text(self, raw: str, metadata: dict) -> ParsedDocument:
        metadata["word_count"] = len(raw.split())
        return ParsedDocument(
            content=raw,
            sections=[{"title": "Content", "content": raw}],
            metadata=metadata,
        )

    def _adf_to_text(self, adf: dict) -> str:
        """Flatten Atlassian Document Format to plain text."""
        parts: list[str] = []
        for block in adf.get("content", []):
            for inline in block.get("content", []):
                if inline.get("type") == "text":
                    parts.append(inline.get("text", ""))
        return " ".join(parts)
