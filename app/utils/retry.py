from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable


async def with_backoff[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float = 0.5,
    retry_after: Callable[[Exception], float | None] | None = None,
) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            explicit_delay = retry_after(exc) if retry_after else None
            delay = explicit_delay if explicit_delay is not None else base_delay * (2**attempt)
            await asyncio.sleep(delay + random.uniform(0, delay * 0.1))
    assert last_error is not None
    raise last_error
