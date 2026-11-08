from __future__ import annotations

import time
from collections.abc import Callable

Clock = Callable[[], int]


def system_clock() -> int:
    return int(time.time())
