#!/usr/bin/env python3
"""打印当前发布模式，供 make-and-publish.ps1 / .sh 解析。

输出一行：<mode>|<default_count>|<中文描述>
- mode: weekend / weekday
- default_count: 该模式默认条数
"""

from __future__ import annotations


def main() -> int:
    from weekend_edu_topics import is_weekend_edu_mode, weekend_default_count
    from cursor_daily_topics import CURSOR_SLOT_ORDER

    if is_weekend_edu_mode():
        n = weekend_default_count()
        print(f"weekend|{n}|周末科普（Opus 选题，默认 {n} 条）")
    else:
        n = len(CURSOR_SLOT_ORDER)
        print(f"weekday|{n}|工作日新闻五槽位（默认 {n} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
