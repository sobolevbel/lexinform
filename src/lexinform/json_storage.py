"""Compact persisted JSON without removing required nulls or unknown fields."""

import json
from collections.abc import Sequence
from typing import cast

from pydantic import BaseModel, JsonValue


def _compact(value: JsonValue, model: object) -> JsonValue:
    if isinstance(value, dict) and isinstance(model, BaseModel):
        fields = type(model).model_fields
        return {
            key: _compact(item, getattr(model, key)) if key in fields else item
            for key, item in value.items()
            if not (item is None and key in fields and fields[key].default is None)
        }
    if isinstance(value, list) and isinstance(model, (list, tuple)):
        return [_compact(item, parsed) for item, parsed in zip(value, model, strict=True)]
    return value


def compact_json(raw: str, model: BaseModel | Sequence[BaseModel]) -> str:
    value: JsonValue = json.loads(raw)
    return json.dumps(_compact(value, model), ensure_ascii=False, separators=(",", ":"))


def storage_json(model: BaseModel | Sequence[BaseModel]) -> str:
    raw = (
        model.model_dump_json()
        if isinstance(model, BaseModel)
        else json.dumps([item.model_dump(mode="json") for item in model], ensure_ascii=False)
    )
    return compact_json(raw, model)


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def read_json(raw: str) -> JsonValue:
    return cast(
        JsonValue,
        json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant),
    )


def canonical_json(value: JsonValue) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
