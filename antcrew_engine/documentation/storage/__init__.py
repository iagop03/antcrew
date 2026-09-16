"""Storage backends for the documentation system."""
from .base import BaseStorage
from .git import GitStorage
from .local import LocalFileStorage
from .s3 import S3Storage

__all__ = ["BaseStorage", "LocalFileStorage", "GitStorage", "S3Storage"]
