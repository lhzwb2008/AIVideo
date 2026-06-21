#!/usr/bin/env python3
"""Print default video count for make-and-publish.ps1 (ASCII output only)."""

from __future__ import annotations

from cursor_daily_topics import CURSOR_SLOT_ORDER
from weekend_edu_topics import is_weekend_edu_mode, weekend_default_count


def main() -> None:
    if is_weekend_edu_mode():
        print(f"weekend|{weekend_default_count()}")
    else:
        print(f"weekday|{len(CURSOR_SLOT_ORDER)}")


if __name__ == "__main__":
    main()
