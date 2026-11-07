import json

from lexinform.models.content import original_delivery_bill_snapshot


def test_original_notice_snapshot_omits_operational_document_bodies() -> None:
    record = {
        "context": {
            "documents": [
                {"identity": "official", "sha256": "pin", "text": "source", "body": "raw"}
            ]
        },
        "review": {"summary": "retained result"},
    }
    raw = json.dumps(
        {
            "ready_analysis": {"text": "prepared text"},
            "consultation_review": record,
            "act_application_review": record,
        }
    )
    snapshot = original_delivery_bill_snapshot(raw)
    value = json.loads(snapshot.payload_json)
    assert "ready_analysis" not in value
    for role in ("consultation_review", "act_application_review"):
        assert value[role]["context"]["documents"] == [
            {"identity": "official", "sha256": "pin", "text": "", "body": ""}
        ]
        assert value[role]["review"] == record["review"]
    assert original_delivery_bill_snapshot(snapshot.payload_json) == snapshot
