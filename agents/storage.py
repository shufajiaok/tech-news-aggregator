"""存储Agent — 写入Supabase数据库 + 提供API查询接口

职责:
1. 将结构化新闻写入Supabase PostgreSQL
2. 记录已处理的tweet_id（防止重复入库）
3. 封装查询方法供API调用
"""

import logging
from datetime import datetime, timezone
from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY
from models import StructuredNews

logger = logging.getLogger(__name__)


class NewsStorage:
    """新闻存储管理器"""

    def __init__(self):
        self.client: Client | None = None
        self._connected = False
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
                self._connected = True
                logger.info("存储Agent: Supabase连接成功")
            except Exception as e:
                logger.error(f"存储Agent: Supabase连接失败 — {e}")
        else:
            logger.warning("存储Agent: 未配置Supabase凭证，将使用内存存储")

        self._memory_store: list[dict] = []  # 内存兜底
        self._processed_tweets: set[str] = set()

    # ── 写入操作 ────────────────────────────────────

    def is_processed(self, tweet_id: str) -> bool:
        """检查推文是否已处理"""
        if tweet_id in self._processed_tweets:
            return True

        if self._connected and self.client:
            try:
                resp = self.client.table("processed_tweets") \
                    .select("tweet_id") \
                    .eq("tweet_id", tweet_id) \
                    .execute()
                if resp.data:
                    self._processed_tweets.add(tweet_id)
                    return True
            except Exception as e:
                logger.error(f"存储Agent: 查询已处理状态失败 — {e}")
        return False

    def mark_processed(self, tweet_id: str):
        """标记推文为已处理"""
        self._processed_tweets.add(tweet_id)
        if self._connected and self.client:
            try:
                self.client.table("processed_tweets").insert({
                    "tweet_id": tweet_id,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
            except Exception as e:
                logger.warning(f"存储Agent: 标记已处理失败(可能已存在) — {e}")

    def insert_news(self, news: StructuredNews) -> bool:
        """插入单条新闻"""
        if self.is_processed(news.source_url):
            logger.debug(f"存储Agent: 跳过重复 news_id={news.id}")
            return False

        record = {
            "id": news.id,
            "title": news.title,
            "summary": news.summary,
            "key_points": news.key_points,
            "full_content": news.full_content,
            "ai_summary": news.ai_summary,
            "category": news.category,
            "source": news.source,
            "source_url": news.source_url,
            "original_author": news.original_author,
            "published_at": news.published_at,
            "created_at": news.created_at,
        }

        if self._connected and self.client:
            try:
                self.client.table("tech_news").insert(record).execute()
                self.mark_processed(news.source_url)
                logger.info(f"存储Agent: 写入成功 — {news.title[:40]}")
                return True
            except Exception as e:
                logger.error(f"存储Agent: 写入失败 — {e}")
                return False
        else:
            # 内存兜底
            self._memory_store.append(record)
            self.mark_processed(news.source_url)
            logger.info(f"存储Agent(内存): 写入 — {news.title[:40]}")
            return True

    def insert_batch(self, news_list: list[StructuredNews]) -> int:
        """批量写入新闻，返回成功数量"""
        count = 0
        for news in news_list:
            if self.insert_news(news):
                count += 1
        logger.info(f"存储Agent: 批量写入 {count}/{len(news_list)} 条成功")
        return count

    # ── 查询操作 ────────────────────────────────────

    def query_recent(
        self,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """查询最近新闻（支持分类筛选+分页）"""
        if self._connected and self.client:
            try:
                query = self.client.table("tech_news") \
                    .select("*") \
                    .order("published_at", desc=True) \
                    .limit(limit) \
                    .offset(offset)
                if category:
                    query = query.eq("category", category)
                resp = query.execute()
                return resp.data or []
            except Exception as e:
                logger.error(f"存储Agent: 查询失败 — {e}")

        # 内存兜底
        results = self._memory_store
        if category:
            results = [r for r in results if r["category"] == category]
        results = sorted(results, key=lambda r: r.get("published_at", ""), reverse=True)
        return results[offset:offset + limit]

    def query_by_id(self, news_id: str) -> dict | None:
        """按ID查询单条新闻"""
        if self._connected and self.client:
            try:
                resp = self.client.table("tech_news") \
                    .select("*") \
                    .eq("id", news_id) \
                    .execute()
                if resp.data:
                    return resp.data[0]
            except Exception as e:
                logger.error(f"存储Agent: 按ID查询失败 — {e}")
        for r in self._memory_store:
            if r["id"] == news_id:
                return r
        return None

    def get_categories(self) -> list[str]:
        """获取所有分类及数量"""
        if self._connected and self.client:
            try:
                resp = self.client.table("tech_news").select("category").execute()
                counts = {}
                for r in (resp.data or []):
                    c = r["category"]
                    counts[c] = counts.get(c, 0) + 1
                return counts
            except Exception as e:
                logger.error(f"存储Agent: 获取分类统计失败 — {e}")
        counts = {}
        for r in self._memory_store:
            c = r["category"]
            counts[c] = counts.get(c, 0) + 1
        return counts


# ── 全局单例 ──────────────────────────────────────
_storage_instance: NewsStorage | None = None


def get_storage() -> NewsStorage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = NewsStorage()
    return _storage_instance


def run_storage(news_list: list[StructuredNews]) -> int:
    """存储Agent入口"""
    storage = get_storage()
    return storage.insert_batch(news_list)