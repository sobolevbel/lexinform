"""Compact persisted JSON without removing required nulls or unknown fields."""

import json
from collections.abc import Sequence

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
