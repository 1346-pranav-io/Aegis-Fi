"""Row validation with quarantine; invalid rows are never silently dropped (FR-ING-02)."""

from collections.abc import Iterable, Iterator
from typing import Any

from pydantic import BaseModel, ValidationError

from aegis.domain.source import QuarantineRecord


def validate_rows[M: BaseModel](
    rows: Iterable[dict[str, Any]], model: type[M]
) -> Iterator[M | QuarantineRecord]:
    for row in rows:
        try:
            yield model.model_validate(row)
        except ValidationError as exc:
            yield QuarantineRecord(
                raw_record=repr(row),
                error_code="SCHEMA_VALIDATION",
                error_detail=str(exc.errors(include_input=False))[:2000],
            )
