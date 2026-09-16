"""Base parser interface for all document formats."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ParsedDocument:
    content: str
    sections: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> ParsedDocument: ...

    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        p = Path(file_path)
        return {
            "file_name": p.name,
            "file_path": str(p),
            "extension": p.suffix.lower(),
        }
