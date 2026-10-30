"""Expose the shared frozen-primary-context source audit without activating a producer."""

from lexinform.content_sources import ContentSourceAudit, inspect_primary_content_sources

__all__ = ["ContentSourceAudit", "inspect_primary_content_sources"]
