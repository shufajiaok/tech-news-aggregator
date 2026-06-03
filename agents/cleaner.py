"""清洗Agent — 过滤科技相关 + 去重

两步流水线:
1. 关键词过滤 — 只保留包含科技关键词的推文
2. SimHash去重 — 过滤高度相似的重复内容
"""

import re
import logging
from simhash import Simhash

from config import TECH_KEYWORDS
from models import RawTweet, CleanedNews

logger = logging.getLogger(__name__)

# SimHash距离阈值 (<3 视为重复)
SIMHASH_THRESHOLD = 3


class NewsCleaner:
    """新闻清洗器: 过滤 + 去重"""

    def __init__(self, keywords: list[str] | None = None):
        self.keywords = keywords or TECH_KEYWORDS
        self._seen_hashes: list[tuple[str, Simhash]] = []  # (tweet_id, simhash)

    def _contains_tech_keyword(self, text: str) -> bool:
        """检查文本是否包含至少一个科技关键词（大小写不敏感）"""
        text_lower = text.lower()
        for kw in self.keywords:
            if kw.lower() in text_lower:
                return True
        return False

    def _compute_simhash(self, text: str) -> Simhash:
        """计算文本的SimHash指纹"""
        # 预处理：分词（英文按空格，中文保留）
        tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', text.lower())
        return Simhash(tokens)

    def _is_duplicate(self, tweet_id: str, text: str) -> bool:
        """检查是否与已处理推文重复"""
        current_hash = self._compute_simhash(text)
        for seen_id, seen_hash in self._seen_hashes:
            distance = current_hash.distance(seen_hash)
            if distance <= SIMHASH_THRESHOLD:
                logger.debug(f"去重: {tweet_id} 与 {seen_id} 距离={distance}")
                return True
        self._seen_hashes.append((tweet_id, current_hash))
        return False

    def filter_by_keywords(self, tweets: list[RawTweet]) -> list[RawTweet]:
        """关键词过滤"""
        filtered = []
        for t in tweets:
            if self._contains_tech_keyword(t.text):
                filtered.append(t)
            else:
                logger.debug(f"过滤(非科技): @{t.author} — {t.text[:60]}...")
        logger.info(f"清洗Agent: 关键词过滤 {len(tweets)} -> {len(filtered)} 条")
        return filtered

    def deduplicate(self, tweets: list[RawTweet]) -> list[RawTweet]:
        """SimHash去重"""
        unique = []
        for t in tweets:
            if not self._is_duplicate(t.tweet_id, t.text):
                unique.append(t)
        logger.info(f"清洗Agent: 去重 {len(tweets)} -> {len(unique)} 条")
        return unique

    def clean(self, tweets: list[RawTweet]) -> list[CleanedNews]:
        """完整清洗流水线: 过滤 -> 去重 -> 输出CleanedNews"""
        # Step 1: 关键词过滤
        filtered = self.filter_by_keywords(tweets)

        # Step 2: SimHash去重
        deduped = self.deduplicate(filtered)

        # Step 3: 转换为CleanedNews
        cleaned = [
            CleanedNews(
                tweet_id=t.tweet_id,
                author=t.author,
                author_username=t.author_username,
                text=t.text,
                url=t.url,
                published_at=t.published_at,
            )
            for t in deduped
        ]
        logger.info(f"清洗Agent: 最终输出 {len(cleaned)} 条清洗后新闻")
        return cleaned


def run_cleaner(tweets: list[RawTweet]) -> list[CleanedNews]:
    """清洗Agent入口"""
    cleaner = NewsCleaner()
    return cleaner.clean(tweets)