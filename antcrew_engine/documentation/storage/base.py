"""Abstract storage interface."""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseStorage(ABC):
    @abstractmethod
    def save(self, doc_id: str, content: bytes, metadata: dict) -> None: ...

    @abstractmethod
    def load(self, doc_id: str) -> bytes: ...

    @abstractmethod
    def list_documents(self, prefix: str | None = None) -> list[str]: ...

    @abstractmethod
    def delete(self, doc_id: str) -> None: ...
