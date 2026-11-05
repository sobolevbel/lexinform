# Frozen status updates and original amendment input status

New status-update plans exclude operational `review_work` using the same Bill
serialization as other prepared messages. The review queue in the current bill
remains intact. The frozen plan retains `consultation_review`, ready analysis and
all other Bill fields. Existing plans keep their stored payload.

A replay of the saved digest-only publication 244 exercised the native preparation
path. The full Bill matched the old input and the projected Bill differed only in
`review_work`. The route and the 443-character rendered text matched. A counterfactual
replacement of this one stored row changed the SQL dump from 25,815,220 to 23,512,713
bytes and the gzip from 5,208,734 to 4,651,623 bytes. The checkpoint was read in a
scratch database and left intact. [Original preparation](review-work-off.json),
[projected preparation](review-work-on.json).

The original page of a confirmed single-message amendment's retained history can
show whether its original request is available, absent from legacy history or invalid.
The response contains a status only. It does not show the question, URL or snapshot ID.
The ordinary delivery response, unrelated history and unavailable pages matched the
previous serialized and rendered output. [History response parity](history-parity.json).
