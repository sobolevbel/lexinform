"""Following published bills until the act is in force.

`StatusTrackingService.check_updates` is the phase entry point. The work is split by concern:
`service` (the loop and stage/text detection), `pre_print` (RPW entries that get a print number
or are withdrawn), `acts` (Dziennik Ustaw and the in-force reminder), `consultations` (the
reminder before a public consultation closes), `posting` (replies under the card, recorded as
pending before sending) and `stages` (enrichment and the dedupe key).
"""

from lexinform.services.tracking.result import TrackingResult
from lexinform.services.tracking.service import StatusTrackingService, TrackingOptions

__all__ = ["StatusTrackingService", "TrackingOptions", "TrackingResult"]
