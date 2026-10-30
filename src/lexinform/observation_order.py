"""Neutral input lifetimes assign observation order before worker submission."""

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, suppress
from contextvars import ContextVar
from threading import Lock

_CURRENT: ContextVar[ObservationInput | None] = ContextVar("observation_input", default=None)


class ObservationInput:
    def __init__(self, ordering: ObservationOrder, order: tuple[int, ...]) -> None:
        self.ordering = ordering
        self.order = order
        self.epoch = ordering.epoch
        self.active = False
        self.thread: int | None = None
        self.children = 0

    def child(self) -> tuple[int, ...]:
        value = self.order + (self.children,)
        self.children += 1
        return value


class ObservationOrder:
    def __init__(self) -> None:
        self._lock = Lock()
        self._phase = 0
        self.epoch = 0

    def reset(self) -> None:
        with self._lock:
            self._phase = 0
            self.epoch += 1

    def current(self) -> ObservationInput | None:
        current = _CURRENT.get()
        return (
            current
            if (
                current is not None
                and current.ordering is self
                and current.epoch == self.epoch
                and current.active
                and current.thread == threading.get_ident()
            )
            else None
        )

    def prefix(self, child: Callable[[], tuple[int, ...]] | None = None) -> tuple[int, ...]:
        current = self.current()
        with self._lock:
            if current is not None:
                return child() if child is not None else ObservationInput.child(current)
            value = (self._phase,)
            self._phase += 1
            return value

    def next(self, child: Callable[[], tuple[int, ...]] | None = None) -> tuple[int, ...]:
        current = self.current()
        prefix = self.prefix(child)
        return prefix if current is not None else prefix + (0,)

    def reserve(self, count: int) -> tuple[ObservationInput, ...]:
        prefix = self.prefix()
        return tuple(ObservationInput(self, prefix + (index,)) for index in range(count))

    @contextmanager
    def input(self, slot: ObservationInput | None) -> Iterator[None]:
        token = _CURRENT.set(slot)
        if slot is not None:
            slot.active = True
            slot.thread = threading.get_ident()
        try:
            yield
        finally:
            if slot is not None:
                slot.active = False
                slot.thread = None
            _CURRENT.reset(token)

    @contextmanager
    def protected_input(
        self,
        slot: ObservationInput | None,
        factory: Callable[[], AbstractContextManager[None]],
    ) -> Iterator[bool]:
        token = _CURRENT.set(_CURRENT.get() if slot is not None else None)
        stack = ExitStack()
        entered = False
        try:
            try:
                stack.enter_context(factory())
                entered = True
            except BaseException:
                _CURRENT.set(None)
                with suppress(BaseException):
                    stack.enter_context(self.input(slot))
            yield entered
        finally:
            with suppress(BaseException):
                stack.close()
            if slot is not None:
                slot.active = False
                slot.thread = None
            _CURRENT.reset(token)
