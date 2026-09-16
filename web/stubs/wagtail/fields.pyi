from typing import Any

from django.db import models

class StreamField(models.Field[Any, Any]):
    def __init__(self, block_types: list[tuple[str, Any]], **kwargs: Any) -> None: ...
