"""Git-backed storage — wraps LocalFileStorage with auto-commit on every write."""
from __future__ import annotations

from .local import LocalFileStorage


class GitStorage(LocalFileStorage):
    def __init__(self, root: str = "./documentation", author: str = "antcrew-doc-system") -> None:
        super().__init__(root)
        self.author = author
        self._repo = None
        self._init_repo()

    def _init_repo(self) -> None:
        try:
            import git
            try:
                self._repo = git.Repo(self.root, search_parent_directories=True)
            except git.InvalidGitRepositoryError:
                self._repo = git.Repo.init(self.root)
        except ImportError:
            pass  # gitpython not installed — silent fallback to plain local storage

    def _commit(self, message: str) -> None:
        if self._repo is None:
            return
        try:
            self._repo.index.add([str(self.root.resolve())])
            has_staged = bool(self._repo.index.diff("HEAD") if not self._repo.bare else [])
            has_untracked = bool(self._repo.untracked_files)
            if has_staged or has_untracked:
                actor_name = self.author
                try:
                    actor_name = self._repo.config_reader().get_value("user", "name", self.author)
                except Exception:
                    pass
                self._repo.index.commit(message)
        except Exception:
            pass

    def save(self, doc_id: str, content: bytes, metadata: dict) -> None:
        super().save(doc_id, content, metadata)
        self._commit(f"docs: add {doc_id}")

    def delete(self, doc_id: str) -> None:
        super().delete(doc_id)
        self._commit(f"docs: remove {doc_id}")
