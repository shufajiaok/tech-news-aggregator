"""导出 Agent — 从 Supabase 读取全部新闻，导出 data.json 供静态消费

职责:
1. 查询 tech_news 表，按 published_at 倒序取最近 EXPORT_MAX_ITEMS 条
2. 写入仓库根目录 data.json（精简格式，同 to_api_dict）
3. 供 GitHub Actions 提交回仓库，供 App / 其他客户端拉取

用法:
    python export_json.py
"""

import json
import logging
import os
from datetime import datetime, timezone

from supabase import create_client

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger("export")

EXPORT_MAX_ITEMS = int(os.getenv("EXPORT_MAX_ITEMS", "300"))
OUTPUT_PATH = os.getenv("EXPORT_OUTPUT", "data.json")

logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def run_export() -> int:
    """导出新闻到 data.json，返回导出条数。失败抛异常由上层处理。"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("导出Agent: 未配置 SUPABASE_URL / SUPABASE_KEY，跳过导出")
        return 0

    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    resp = (
        client.table("tech_news")
        .select(
            "id,title,summary,key_points,full_content,ai_summary,"
            "category,source,source_url,original_author,published_at,created_at"
        )
        .order("published_at", desc=True)
        .limit(EXPORT_MAX_ITEMS)
        .execute()
    )
    rows = resp.data or []

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "news": rows,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"导出Agent: 已写入 {OUTPUT_PATH}，共 {len(rows)} 条")
    return len(rows)


if __name__ == "__main__":
    run_export()
