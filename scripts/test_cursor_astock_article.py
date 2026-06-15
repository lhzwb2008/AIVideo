#!/usr/bin/env python3
"""测试 Cursor Cloud Agent：联网搜今日 A 股最热板块并生成专题分析长文。

用法（项目根目录）:
  source .env
  export PYTHONPATH=src
  python3 scripts/test_cursor_astock_article.py

输出: logs/test_cursor_astock_article.md
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cursor_client import create_agent, create_run, model_id, run_with_stream  # noqa: E402

OUT = ROOT / "logs" / "test_cursor_astock_article.md"
META = ROOT / "logs" / "test_cursor_astock_article_meta.json"

PROMPT = """你是 A 股财经专栏作者。请联网搜索「今天」A 股市场（以你搜索到的最新交易日为准）的最热板块或概念。

任务分两步，全部用中文输出到一份 Markdown 文件内容里（不要只给提纲）：

## 第一步：判断今日最热方向（约 200 字）
- 用财联社、新浪财经、东方财富、同花顺、金融界等公开报道交叉验证
- 明确写出：大盘概况（指数涨跌、涨跌家数、成交额是否缩量）、今日 **唯一** 最值得写的最热板块/概念（例如 MLCC、存储芯片、工业气体等，只选一个）
- 说明为什么选它而不是写全盘收评

## 第二步：该方向的专题分析（正文 1500–2500 字）
必须是一篇 **单一板块/概念深度分析**，结构包含：
1. 一句话结论
2. 这个板块是什么、产业链位置（小白能懂）
3. 今天为什么涨（盘面事实 + 消息催化，要有具体数字）
4. 逻辑能持续吗（供需/涨价/政策/海外映射，客观陈述）
5. 和风险提示（3 条，不做荐股、不给买卖点、不写股票代码）
6. 结语

硬性要求：
- 禁止编造未在搜索结果中出现的具体股价、涨停家数；搜不到就写「数据待核实」
- 禁止股票代码、荐股、目标价、买卖建议

请把最终 Markdown 全文作为你的回复输出（不要用「见附件」敷衍）。"""


def main() -> int:
    today = date.today().isoformat()
    print(f"[test] 日期参考: {today}")
    print(f"[test] 模型: {model_id()}")
    print("[test] 创建 Cursor Cloud Agent…")
    agent_id, run_id = create_agent(PROMPT)
    print(f"[test] agent_id={agent_id} run_id={run_id}")

    chunks: list[str] = []

    def on_delta(t: str) -> None:
        chunks.append(t)
        sys.stdout.write(t)
        sys.stdout.flush()

    print("[test] 等待 Agent 完成（可能需数分钟）…")
    text, status = run_with_stream(agent_id, run_id, on_assistant=on_delta)
    print(f"\n[test] 状态: {status}")

    body = (text or "".join(chunks)).strip()
    if not body:
        print("[test] 失败：Agent 未返回正文", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = f"<!-- Cursor Cloud Agent 测试 · {today} · model={model_id()} -->\n\n"
    OUT.write_text(header + body + "\n", encoding="utf-8")
    META.write_text(
        f'{{"agent_id":"{agent_id}","run_id":"{run_id}","status":"{status}","out":"{OUT}"}}\n',
        encoding="utf-8",
    )
    print(f"[test] 已写入 {OUT} ({len(body)} 字)")
    return 0 if status == "FINISHED" else 2


if __name__ == "__main__":
    os.chdir(ROOT)
    if (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    raise SystemExit(main())
