"""Canonical compact context shared by continuation payloads and card history."""

from lexinform.models.bill import Bill


def continuation_snapshot(bill: Bill) -> str:
    return bill.model_dump_json(
        exclude={
            "analysis",
            "consultation_review",
            "act_application_review",
            "review_work",
            "rcl",
            "submission",
            "wykaz",
            "observed_process",
            "agenda",
            "stages",
        }
    )
