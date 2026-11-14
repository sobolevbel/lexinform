"""Report fields declare presentation and activity without changing their stored representation."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from pydantic.fields import FieldInfo


@dataclass(frozen=True)
class ReportField:
    """Unannotated fields are result metrics; context is visible but does not make a run active."""

    section: str | None = "other"
    template: str | None = None
    row: int = 0
    order: int = 0
    role: Literal["result", "context", "metadata"] = "result"
    scope: Literal["execution", "reads", "all"] = "execution"
    context_keys: tuple[str, ...] = ()
    separate_lines: bool = False
    bare_keys: bool = False


def report_field(field: FieldInfo) -> ReportField:
    return next((item for item in field.metadata if isinstance(item, ReportField)), ReportField())


def report_leaves(
    value: object, name: str = "", *, results_only: bool = False
) -> Iterator[tuple[str, object]]:
    if isinstance(value, BaseModel):
        for key, field in type(value).model_fields.items():
            role = report_field(field).role
            if role != "metadata" and (not results_only or role == "result"):
                yield from report_leaves(
                    getattr(value, key),
                    f"{name}.{key}" if name else key,
                    results_only=results_only,
                )
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from report_leaves(
                child, f"{name}.{key}" if name else str(key), results_only=results_only
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value, 1):
            yield from report_leaves(
                child, f"{name}.{index}" if name else str(index), results_only=results_only
            )
    elif value.strip() if isinstance(value, str) else value:
        yield name, value


def has_report_content(value: object, *, results_only: bool = False) -> bool:
    return next(report_leaves(value, results_only=results_only), None) is not None


@dataclass(frozen=True)
class ReportValue:
    name: str
    value: object
    field: ReportField

    @property
    def is_result(self) -> bool:
        if self.field.role != "result":
            return False
        value = self.value
        if isinstance(value, Mapping) and self.field.context_keys:
            value = {
                key: count for key, count in value.items() if key not in self.field.context_keys
            }
        return has_report_content(value, results_only=True)


@dataclass(frozen=True)
class ReportContent:
    values: tuple[ReportValue, ...]

    @property
    def is_empty(self) -> bool:
        return not any(item.is_result for item in self.values)

    def has(self, name: str) -> bool:
        return any(item.name == name for item in self.values)

    @property
    def sections(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(item.field.section for item in self.values if item.field.section)
        )

    def rows(self, section: str) -> tuple[tuple[str, ...], ...]:
        groups: dict[int, list[str]] = {}
        items = sorted(self.values, key=lambda item: (item.field.row, item.field.order))
        for item in items:
            field = item.field
            if field.section != section:
                continue
            name = "" if field.bare_keys else item.name
            for index, (key, value) in enumerate(report_leaves(item.value, name)):
                text = field.template.format(value) if field.template else f"{key}: {value}"
                row = field.row + index if field.separate_lines else field.row
                groups.setdefault(row, []).append(text)
        return tuple(tuple(groups[row]) for row in sorted(groups))


def report_content(
    model: BaseModel, *, execution: bool, overrides: Mapping[str, object] | None = None
) -> ReportContent:
    values = []
    for name, info in type(model).model_fields.items():
        field = report_field(info)
        if field.role == "metadata" or field.scope == ("reads" if execution else "execution"):
            continue
        value = (
            overrides[name] if overrides is not None and name in overrides else getattr(model, name)
        )
        if has_report_content(value):
            values.append(ReportValue(name, value, field))
    return ReportContent(tuple(values))
