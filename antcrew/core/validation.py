from __future__ import annotations

from typing import Any

from pydantic import BaseModel, TypeAdapter


def _validate_schema(schema: Any, data: Any) -> Any:
    """Validate data against schema, supporting both BaseModel subclasses and generic types."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate(data)
    return TypeAdapter(schema).validate_python(data)
