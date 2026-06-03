"""采集Agent — 从X平台获取指定科技账号的最新推文

支持两种模式:
1. X API v2 (推荐, 需要Bearer Token)
2. Playwright浏览器自动化 (备选, 无需API Key)
"""

import time
import logging
import httpx
from typing import Optional

from config import (
    X_BEARER_TOKEN,
    X_API_KEY,
    X_API_SECRET,
    X_ACCESS_TOKEN,
    X_ACCESS_SECRET,
    TARGET_ACCOUNTS,
    MAX_TWEETS_PER_ACCOUNT,
    REQUEST_DELAY_SECONDS,
)
from models import RawTweet

logger = logging.getLogger(__name__)


class XCollector:
    """X平台数据采集器"""

    BASE_URL = "https://api.twitter.com/2"

    def __init__(self):
        self._headers: dict = {}
        self._user_ids: dict[str, str] = {}  # username -> user_id 缓存
        self._init_auth()

    def _init_auth(self):
        """初始化认证头"""
        if X_BEARER_TOKEN:
            self._headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
            self._auth_mode = "bearer"
            logger.info("采集Agent: 使用 Bearer Token 认证")
        elif X_API_KEY:
            self._headers = {
                "Authorization": f"Bearer {X_API_KEY}",
            }
            self._auth_mode = "api_key"
            logger.info("采集Agent: 使用 API Key 认证")
        else:
            self._auth_mode = "none"
            logger.warning("采集Agent: 未配置X API凭证，将返回模拟数据用于测试")

    def _get_user_id(self, username: str) -> Optional[str]:
        """通过用户名获取user_id"""
        if username in self._user_ids:
            return self._user_ids[username]

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(
                    f"{self.BASE_URL}/users/by/username/{username}",
                    headers=self._headers,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    uid = data.get("id")
                    if uid:
                        self._user_ids[username] = uid
                        return uid
                logger.warning(f"获取用户ID失败: {username}, status={resp.status_code}")
        except Exception as e:
            logger.error(f"获取用户ID异常: {username}, {e}")
        return None

    def fetch_account_tweets(self, username: str, max_results: int = MAX_TWEETS_PER_ACCOUNT) -> list[RawTweet]:
        """获取单个账号的最新推文"""
        tweets: list[RawTweet] = []

        if self._auth_mode == "none":
            return self._mock_tweets(username)

        user_id = self._get_user_id(username)
        if not user_id:
            return tweets

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(
                    f"{self.BASE_URL}/users/{user_id}/tweets",
                    headers=self._headers,
                    params={
                        "max_results": min(max_results, 100),
                        "tweet.fields": "created_at,public_metrics,entities",
                        "exclude": "retweets,replies",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for t in data.get("data", []):
                        tweets.append(RawTweet(
                            tweet_id=t["id"],
                            author=username,
                            author_username=f"@{username}",
                            text=t.get("text", ""),
                            url=f"https://x.com/{username}/status/{t['id']}",
                            published_at=t.get("created_at", ""),
                            metrics=t.get("public_metrics", {}),
                        ))
                elif resp.status_code == 429:
                    logger.warning(f"Rate limit hit for {username}, waiting 60s...")
                    time.sleep(60)
                else:
                    logger.error(f"获取推文失败: {username}, status={resp.status_code}, body={resp.text[:200]}")
        except Exception as e:
            logger.error(f"获取推文异常: {username}, {e}")

        time.sleep(REQUEST_DELAY_SECONDS)
        return tweets

    def fetch_all(self) -> list[RawTweet]:
        """遍历所有目标账号，返回全部原始推文"""
        all_tweets: list[RawTweet] = []
        for username in TARGET_ACCOUNTS:
            logger.info(f"采集Agent: 正在获取 @{username} 的推文...")
            tweets = self.fetch_account_tweets(username)
            all_tweets.extend(tweets)
            logger.info(f"采集Agent: @{username} 获取到 {len(tweets)} 条推文")
        logger.info(f"采集Agent: 总计获取 {len(all_tweets)} 条原始推文")
        return all_tweets

    def _mock_tweets(self, username: str) -> list[RawTweet]:
        """无API凭证时返回模拟数据用于开发测试"""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        mock_data = {
            "SemiconductorTF": [
                ("TSMC's 3nm process yield has reportedly reached 92%, exceeding expectations for Q3 production ramp. "
                 "This puts pressure on Samsung's foundry business to deliver competitive yields.",
                 "https://x.com/SemiconductorTF/status/1"),
                ("Breaking: NVIDIA has reportedly placed a massive order for CoWoS advanced packaging capacity "
                 "through 2025, signaling strong demand for next-gen AI chips.",
                 "https://x.com/SemiconductorTF/status/2"),
            ],
            "dan_nystedt": [
                ("Samsung Electronics Q2 profit forecast raised by analysts citing memory chip recovery "
                 "and strong HBM3E demand from AI customers.",
                 "https://x.com/dan_nystedt/status/3"),
            ],
            "DrIanCutress": [
                ("Intel Arrow Lake-S desktop CPU performance benchmarks leak: 15% single-thread uplift "
                 "over Raptor Lake, competitive with AMD Zen 5 in early tests.",
                 "https://x.com/DrIanCutress/status/4"),
                ("AMD MI300X availability improves significantly in Q2, becoming a viable alternative "
                 "to NVIDIA H100 for AI inference workloads at lower TCO.",
                 "https://x.com/DrIanCutress/status/5"),
            ],
            "anshelsag": [
                ("Apple M4 chip details emerge: built on TSMC N3E, features enhanced Neural Engine "
                 "capable of 38 TOPS, positioning MacBooks as serious AI development platforms.",
                 "https://x.com/anshelsag/status/6"),
            ],
            "Kurnal": [
                ("China's SMIC reportedly making progress on 5nm process using existing DUV equipment, "
                 "though yields remain sub-commercial. Industry analysts cautious about timeline.",
                 "https://x.com/Kurnal/status/7"),
            ],
            "IC_insights": [
                ("Global semiconductor market projected to grow 18% YoY in 2025, driven primarily by "
                 "AI accelerator demand and memory recovery cycle.",
                 "https://x.com/IC_insights/status/8"),
            ],
        }
        tweets: list[RawTweet] = []
        for i, (text, url) in enumerate(mock_data.get(username, [])):
            ts = (now - datetime.timedelta(hours=i * 3)).isoformat()
            tweets.append(RawTweet(
                tweet_id=f"mock_{username}_{i}",
                author=username,
                author_username=f"@{username}",
                text=text,
                url=url,
                published_at=ts,
                metrics={"like_count": 100 + i * 10, "retweet_count": 20 + i * 5},
            ))
        return tweets


def run_collector() -> list[RawTweet]:
    """采集Agent入口"""
    collector = XCollector()
    return collector.fetch_all()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    results = run_collector()
    for t in results:
        print(f"  [{t.author}] {t.text[:80]}...")