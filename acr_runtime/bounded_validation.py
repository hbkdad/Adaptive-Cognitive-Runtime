from __future__ import annotations

from .secret_management import assert_secret_free


def bounded_text(
    value: object,
    *,
    field: str,
    maximum: int = 2_000,
) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")
    normalized = value.strip()
    assert_secret_free(normalized, field)
    return normalized


def bounded_text_list(
    value: object,
    *,
    field: str,
    minimum: int = 1,
    maximum: int = 16,
    item_maximum: int = 512,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{field} must contain {minimum} to {maximum} items")
    result = tuple(
        bounded_text(item, field=field, maximum=item_maximum)
        for item in value
    )
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicates")
    return result
