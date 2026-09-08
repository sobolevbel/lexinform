"""Running one I/O-bound step for many items at once, while the service around it stays
sequential.

The rule for using `fan_out`: the function it runs may talk to the network (Sejm API, PDF
downloads, the LLM) but never to the repository. Outcomes are consumed in the calling thread, in
input order, and that is where every database write happens: SQLite sees a single thread, the
`--dry-run` transaction stays intact, and log lines keep their order per bill. With `workers=1`
nothing is threaded at all, so a plain loop and the parallel version behave identically.
"""

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome[T, R]:
    """What happened to one item: either a value or the exception its step raised."""

    item: T
    value: R | None = None
    error: Exception | None = None

    def result(self) -> R:
        """The value, or re-raise the step's exception so the caller classifies it as usual."""
        if self.error is not None:
            raise self.error
        return self.value  # type: ignore[return-value]


def fan_out[T, R](
    items: Iterable[T], step: Callable[[T], R], *, workers: int
) -> Iterator[Outcome[T, R]]:
    """Apply `step` to every item, up to `workers` at a time; yield outcomes in input order.

    Stopping the iteration early (for example on an outage) cancels the items not started yet
    and waits for the running ones, so no request outlives the phase that made it.
    """
    todo = list(items)
    if workers <= 1 or len(todo) <= 1:
        for item in todo:
            yield _attempt(item, step)
        return
    pool = ThreadPoolExecutor(max_workers=min(workers, len(todo)), thread_name_prefix="lexinform")
    try:
        futures = [(item, pool.submit(step, item)) for item in todo]
        for item, future in futures:
            try:
                yield Outcome(item, value=future.result())
            except Exception as exc:
                yield Outcome(item, error=exc)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _attempt[T, R](item: T, step: Callable[[T], R]) -> Outcome[T, R]:
    try:
        return Outcome(item, value=step(item))
    except Exception as exc:
        return Outcome(item, error=exc)
