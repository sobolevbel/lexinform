import json

from pydantic import BaseModel

from lexinform.json_storage import compact_json, storage_json


class StoredChild(BaseModel):
    required_null: str | None
    optional_null: str | None = None
    enabled: bool = False
    count: int = 0


class StoredParent(BaseModel):
    children: list[StoredChild]


def test_compaction_preserves_required_nulls_false_zero_and_unknown_fields() -> None:
    raw = '{"children":[{"required_null":null,"optional_null":null,"enabled":false,"count":0,"future":null}],"unknown":{"value":null}}'
    model = StoredParent.model_validate_json(raw)

    compact = compact_json(raw, model)

    assert StoredParent.model_validate_json(compact) == model
    data = json.loads(compact)
    assert data == {
        "children": [{"required_null": None, "enabled": False, "count": 0, "future": None}],
        "unknown": {"value": None},
    }
    assert storage_json(model) == '{"children":[{"required_null":null,"enabled":false,"count":0}]}'
