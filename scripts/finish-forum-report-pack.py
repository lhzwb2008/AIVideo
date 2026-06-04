#!/usr/bin/env python3
"""为已有 post.md 补全报告配图 + article.docx（不覆盖正文）。

注意：主流程 build_forum_pack 使用视频分镜图，不会调用本脚本。
本脚本仅用于手动把 images/ 换成 Bloomberg 风信息图（与漫画分镜叙事易脱节）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from forum_manual_pack import generate_report_figures, write_docx_from_post_md
from research import load_env


def main() -> int:
    load_env()
    if len(sys.argv) < 2:
        print("用法: python3 scripts/finish-forum-report-pack.py <forum_pack_dir>")
        return 1
    pack_dir = Path(sys.argv[1])
    if not pack_dir.is_dir():
        print(f"目录不存在: {pack_dir}")
        return 1
    post_md = pack_dir / "post.md"
    if not post_md.is_file():
        print(f"缺少 post.md: {post_md}")
        return 1

    print(f"=== 报告配图（最多3张）→ {pack_dir}/images/ ===")
    paths = generate_report_figures(pack_dir, max_figures=3)
    print(f"生成 {len(paths)} 张配图")

    print(f"=== 导出 Word → {pack_dir}/article.docx ===")
    write_docx_from_post_md(post_md, pack_dir / "article.docx", pack_dir=pack_dir)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
