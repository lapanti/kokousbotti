"""The bot's only output channel.

This repository is public, and so are its Actions logs. Everything printed here is readable
by anyone, so the log carries counters, HTTP status codes and exception type names only —
never a name, a Discord id, a meeting title, a message body or an API error body.
"""

from __future__ import annotations

import re
import sys

_SAFE_KEY = re.compile(r"^[a-z_]{1,40}$")


def counts(**values: int) -> None:
    """One line of integer counters, e.g. `uusia=1 postattu=1 tagi_puuttuu=0`."""
    parts = []
    for key, value in values.items():
        if not _SAFE_KEY.match(key) or not isinstance(value, int):
            raise ValueError("log.counts takes lowercase keys and int values only")
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


def error(kind: str, status: int | None = None) -> None:
    """An error by kind (an exception class name) and optional HTTP status. No message text:
    exception messages can carry URLs, ids or response bodies."""
    kind = kind if _SAFE_KEY.match(kind.lower()) else "Error"
    print(f"virhe={kind}" + (f" status={status}" if status is not None else ""),
          file=sys.stderr, flush=True)
