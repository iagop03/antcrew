"""COBOLNormalizer — re-exported from polytranslate for backwards compatibility.

The implementation now lives in polytranslate.utils.cobol_normalizer.
Requires: pip install 'antcrew[java-to-cobol]'
"""
from __future__ import annotations

try:
    from polytranslate.utils.cobol_normalizer import (  # noqa: F401
        COBOLNormalizer,
        _camel_to_upper_dashed,
    )
except ImportError as _e:
    raise ImportError(
        "COBOLNormalizer is part of polytranslate.\n"
        "Install it with: pip install 'antcrew[java-to-cobol]'"
    ) from _e

__all__ = ["COBOLNormalizer", "_camel_to_upper_dashed"]
