"""采集Agent — RSS/Web爬虫获取科技新闻

从多个RSS源和科技网站抓取新闻，支持:
- RSS/Atom Feed解析
- HTML页面抓取 (备选)
- 代理支持
- 随机延迟防封
- 优雅降级: 网络不可用时返回模拟数据
"""

import random
import time
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import feedparser

from config import (
    NEWS_SOURCES,
    HTTP_PROXY,
    HTTPS_PROXY,
    CRAWL_DELAY_MIN,
    CRAWL_DELAY_MAX,
    CRAWL_TIMEOUT,
    MAX_ARTICLES_PER_SOURCE,
    USER_AGENT,
)
from models import RawTweet

logger = logging.getLogger(__name__)


class WebCollector:
    """Web爬虫采集器 — RSS + HTML"""

    def __init__(self):
        self._session: Optional[httpx.Client] = None
        self._available = False

    def _build_client(self) -> httpx.Client:
        """构建带代理的HTTP客户端"""
        proxies = None
        if HTTP_PROXY or HTTPS_PROXY:
            proxies = {}
            if HTTP_PROXY:
                proxies["http://"] = HTTP_PROXY
            if HTTPS_PROXY:
                proxies["https://"] = HTTPS_PROXY
            logger.info(f"采集Agent: 使用代理 {list(proxies.values())[0] if proxies else 'none'}")

        return httpx.Client(
            timeout=CRAWL_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            },
            proxy=proxies.get("https://") or proxies.get("http://") if proxies else None,
        )

    def _random_delay(self):
        """随机延迟，防止被封"""
        delay = random.uniform(CRAWL_DELAY_MIN, CRAWL_DELAY_MAX)
        time.sleep(delay)

    def _make_id(self, source_name: str, url: str) -> str:
        """生成唯一tweet_id"""
        raw = f"{source_name}:{url}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _parse_date(self, entry) -> str:
        """从feed条目提取发布时间"""
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    from time import mktime
                    dt = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
                    return dt.isoformat()
                except Exception:
                    pass
        # 兜底: 用当前时间减随机偏移
        return datetime.now(timezone.utc).isoformat()

    def _clean_html(self, text: str) -> str:
        """去除HTML标签"""
        import re
        clean = re.sub(r'<[^>]+>', '', text)
        clean = re.sub(r'\s+', ' ', clean)
        return clean.strip()

    def fetch_rss(self, source: dict) -> list[RawTweet]:
        """抓取单个RSS源"""
        articles: list[RawTweet] = []
        name = source["name"]
        url = source["url"]

        try:
            client = self._build_client()
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f"采集Agent: RSS请求失败 [{name}] HTTP {resp.status_code}")
                return articles

            feed = feedparser.parse(resp.text)
            if feed.bozo and not feed.entries:
                logger.warning(f"采集Agent: RSS解析失败 [{name}]: {feed.bozo_exception}")
                return articles

            entries = feed.entries[:MAX_ARTICLES_PER_SOURCE]
            for entry in entries:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link = entry.get("link", "")

                if not title or not link:
                    continue

                text = f"{title}. {self._clean_html(summary)}" if summary else title
                published = self._parse_date(entry)

                articles.append(RawTweet(
                    tweet_id=self._make_id(name, link),
                    author=name,
                    author_username=name,
                    text=text[:500],
                    url=link,
                    published_at=published,
                ))

            logger.info(f"采集Agent: [{name}] RSS获取 {len(articles)} 篇")
            self._available = True

        except httpx.ConnectError as e:
            logger.warning(f"采集Agent: [{name}] 连接失败 — {e}")
        except httpx.TimeoutException:
            logger.warning(f"采集Agent: [{name}] 请求超时")
        except Exception as e:
            logger.error(f"采集Agent: [{name}] 异常 — {type(e).__name__}: {e}")

        self._random_delay()
        return articles

    def fetch_all(self) -> list[RawTweet]:
        """遍历所有新闻源"""
        all_articles: list[RawTweet] = []
        total = len(NEWS_SOURCES)

        for i, source in enumerate(NEWS_SOURCES):
            name = source["name"]
            logger.info(f"采集Agent: [{i+1}/{total}] 正在抓取 {name}...")

            if source["type"] == "rss":
                articles = self.fetch_rss(source)
            else:
                articles = self.fetch_rss(source)  # 统一走RSS

            all_articles.extend(articles)

        logger.info(f"采集Agent: 总计获取 {len(all_articles)} 篇文章")

        if not self._available:
            logger.warning("采集Agent: 所有源均不可达，使用模拟数据")
            return self._mock_articles()

        return all_articles

    def _mock_articles(self) -> list[RawTweet]:
        """网络不可用时的模拟数据"""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        mock_articles = [
            ("TSMC 3nm Yield Reportedly Exceeds 92%, Surpassing Q3 Ramp Expectations",
             "TSMC's N3 process node has achieved yields above 92%, exceeding internal targets for Q3 production ramp. This milestone puts significant pressure on Samsung Foundry to deliver competitive yields on its 3nm GAA process.",
             "https://www.anandtech.com/tsmc-3nm-yield-92", "Foundry",
             now),
            ("NVIDIA Places Massive CoWoS Advanced Packaging Order Through 2026",
             "NVIDIA has reportedly secured a substantial portion of TSMC's CoWoS advanced packaging capacity through 2026, signaling continued explosive demand for its next-generation AI accelerators including Blackwell Ultra and Rubin.",
             "https://www.tomshardware.com/nvidia-cowos-order-2026", "GPU",
             now),
            ("Intel Arrow Lake-S Benchmarks Leak: 15% Single-Thread Uplift",
             "Early benchmarks of Intel's Arrow Lake-S desktop processors show approximately 15% single-thread performance improvement over Raptor Lake, positioning it competitively against AMD's Zen 5 lineup in the upcoming desktop CPU battle.",
             "https://wccftech.com/intel-arrow-lake-s-benchmark-leak", "CPU",
             now),
            ("AMD MI300X Availability Improves as Viable H100 Alternative",
             "AMD's MI300X datacenter GPU availability has significantly improved in Q2, with cloud providers reporting it as a cost-effective alternative to NVIDIA H100 for AI inference workloads, offering up to 30% lower TCO.",
             "https://www.semianalysis.com/amd-mi300x-availability", "AI",
             now),
            ("China's SMIC Makes Progress on 5nm Using DUV Equipment",
             "SMIC reportedly making incremental progress on 5nm process development using existing DUV lithography equipment, though yields remain below commercially viable levels. Industry analysts remain cautious about production timeline.",
             "https://semiengineering.com/smic-5nm-duv-progress", "Foundry",
             now),
            ("Samsung HBM3E Memory Qualified for NVIDIA's Next-Gen GPUs",
             "Samsung Electronics has reportedly passed qualification for its HBM3E memory with NVIDIA, paving the way for supply into next-generation AI accelerators and easing the memory bottleneck in AI computing.",
             "https://www.semiconductor-digest.com/samsung-hbm3e-nvidia", "Semiconductor",
             now),
            ("Apple M4 Chip Details: TSMC N3E, 38 TOPS Neural Engine",
             "Apple's M4 processor, built on TSMC's N3E process, features an enhanced Neural Engine capable of 38 trillion operations per second (TOPS), positioning upcoming MacBooks as serious platforms for local AI development and inference.",
             "https://www.theverge.com/apple-m4-chip-details", "Digital",
             now),
            ("Global Semiconductor Market Forecast: 18% Growth in 2025",
             "Industry analysts project the global semiconductor market to grow 18% year-over-year in 2025, driven primarily by AI accelerator demand, memory market recovery, and increased adoption of advanced packaging technologies.",
             "https://www.techspot.com/semiconductor-market-2025-forecast", "Semiconductor",
             now),
        ]

        articles = []
        for i, (title, text, url, cat, base_time) in enumerate(mock_articles):
            ts = (base_time - timedelta(hours=i * 2)).isoformat()
            articles.append(RawTweet(
                tweet_id=f"mock_{i}",
                author=f"TechSource-{i}",
                author_username=f"techsource{i}",
                text=f"{title}. {text}",
                url=url,
                published_at=ts,
            ))
        return articles


def run_collector() -> list[RawTweet]:
    """采集Agent入口"""
    collector = WebCollector()
    return collector.fetch_all()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    results = run_collector()
    for t in results:
        print(f"  [{t.author}] {t.text[:80]}...")
        print(f"    URL: {t.url}")
        print()