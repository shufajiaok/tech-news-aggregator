"""采集Agent — RSS/Web爬虫获取科技新闻

从多个RSS源和科技网站抓取新闻，支持:
- RSS/Atom Feed解析
- content:encoded / content 正文提取（优先于summary）
- 原文网页正文抓取（trafilatura + BeautifulSoup 降级）
- 代理支持
- 随机延迟防封
- 优雅降级: 网络不可用时返回模拟数据
"""

import random
import time
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
import feedparser
from bs4 import BeautifulSoup

from config import (
    NEWS_SOURCES,
    HTTP_PROXY,
    HTTPS_PROXY,
    CRAWL_DELAY_MIN,
    CRAWL_DELAY_MAX,
    CRAWL_TIMEOUT,
    MAX_ARTICLES_PER_SOURCE,
    USER_AGENT,
    FULL_CONTENT_TIMEOUT,
    FULL_CONTENT_MAX_LENGTH,
    FULL_CONTENT_FETCH_ENABLED,
)
from models import RawTweet

logger = logging.getLogger(__name__)


class WebCollector:
    """Web爬虫采集器 — RSS + HTML"""

    def __init__(self):
        self._session: Optional[httpx.Client] = None
        self._available = False

    def _build_client(self, timeout: int = CRAWL_TIMEOUT) -> httpx.Client:
        """构建带代理的HTTP客户端"""
        proxies = None
        if HTTP_PROXY or HTTPS_PROXY:
            proxies = {}
            if HTTP_PROXY:
                proxies["http://"] = HTTP_PROXY
            if HTTPS_PROXY:
                proxies["https://"] = HTTPS_PROXY

        return httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "application/rss+xml,application/atom+xml;q=0.8,*/*;q=0.7"
                ),
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
        return datetime.now(timezone.utc).isoformat()

    def _clean_html(self, text: str) -> str:
        """去除HTML标签，保留文本"""
        clean = re.sub(r'<[^>]+>', ' ', text)
        clean = re.sub(r'\s+', ' ', clean)
        return clean.strip()

    # ── RSS正文提取 ──────────────────────────────────

    def _extract_rss_content(self, entry) -> str:
        """从RSS条目中提取正文内容。

        优先级: content:encoded > content > description > summary
        均尝试提取完整的HTML正文，然后清洗为纯文本。
        """
        # 1. content:encoded（常见于WordPress RSS）
        for content_field in entry.get("content", []):
            value = content_field.get("value", "")
            if value and len(value) > 100:
                return self._clean_html(value)

        # 2. content 字段（Atom格式）
        content_attr = getattr(entry, "content", None)
        if content_attr:
            if isinstance(content_attr, list):
                for c in content_attr:
                    val = c.get("value", "") if isinstance(c, dict) else str(c)
                    if val and len(val) > 100:
                        return self._clean_html(val)
            elif isinstance(content_attr, str) and len(content_attr) > 100:
                return self._clean_html(content_attr)

        # 3. content:encoded 通过其他键名
        for key in ("content_encoded", "encoded", "body"):
            val = entry.get(key, "")
            if val and len(val) > 100:
                return self._clean_html(val)

        # 4. summary / description（降级）
        summary = entry.get("summary", entry.get("description", ""))
        if summary:
            cleaned = self._clean_html(summary)
            if len(cleaned) > 50:
                return cleaned

        return ""

    def _is_substantial_content(self, content: str) -> bool:
        """判断内容是否足够丰富（不只是摘要级别的短文本）"""
        if not content:
            return False
        return len(content) >= 200

    # ── 原文网页正文抓取 ─────────────────────────────

    def _fetch_full_article(self, url: str, rss_content: str = "") -> str:
        """从原文URL抓取正文。

        使用 trafilatura 做正文提取（去除导航、广告、评论区），
        失败时降级到 BeautifulSoup 手动提取。
        """
        if not FULL_CONTENT_FETCH_ENABLED:
            return rss_content

        try:
            client = self._build_client(timeout=FULL_CONTENT_TIMEOUT)
            resp = client.get(url)
            if resp.status_code != 200:
                logger.debug(f"正文抓取: HTTP {resp.status_code} — {url[:60]}")
                return rss_content

            html = resp.text
            if not html or len(html) < 200:
                return rss_content

            # 尝试 trafilatura 提取
            content = self._extract_with_trafilatura(html, url)
            if content and self._is_substantial_content(content):
                logger.debug(f"正文抓取(trafilatura): {len(content)} 字符 — {url[:60]}")
                return content[:FULL_CONTENT_MAX_LENGTH]

            # 降级: BeautifulSoup 手动提取
            content = self._extract_with_bs4(html)
            if content and self._is_substantial_content(content):
                logger.debug(f"正文抓取(bs4降级): {len(content)} 字符 — {url[:60]}")
                return content[:FULL_CONTENT_MAX_LENGTH]

            logger.debug(f"正文抓取: 未能提取到足够内容 — {url[:60]}")
            return rss_content

        except httpx.ConnectError:
            logger.debug(f"正文抓取: 连接失败 — {url[:60]}")
        except httpx.TimeoutException:
            logger.debug(f"正文抓取: 超时 — {url[:60]}")
        except Exception as e:
            logger.debug(f"正文抓取: 异常 — {type(e).__name__}: {e}")

        return rss_content

    def _extract_with_trafilatura(self, html: str, url: str = "") -> str:
        """使用 trafilatura 提取正文（去除导航/广告/评论区）"""
        try:
            import trafilatura
            extracted = trafilatura.extract(
                html,
                url=url,
                favor_precision=True,
                include_comments=False,
                include_tables=False,
                deduplicate=True,
            )
            return (extracted or "").strip()
        except ImportError:
            return ""
        except Exception as e:
            logger.debug(f"trafilatura 提取失败: {e}")
            return ""

    def _extract_with_bs4(self, html: str) -> str:
        """使用 BeautifulSoup 手动提取正文（降级方案）。

        尝试常见正文容器（article、main、content等），
        去除 nav、footer、aside、script、style 等无关元素。
        """
        try:
            soup = BeautifulSoup(html, "lxml")

            # 移除无关元素
            for tag_name in (
                "nav", "footer", "aside", "header", "script", "style",
                "noscript", "iframe", "form", "ins", "figure",
            ):
                for tag in soup.find_all(tag_name):
                    tag.decompose()

            # 移除常见评论区/广告类名
            for cls in (
                "comment", "comments", "sidebar", "advertisement", "ad-",
                "social-share", "related-posts", "recommended", "widget",
                "nav-", "footer-", "header-", "menu", "share-",
            ):
                for tag in soup.find_all(class_=re.compile(cls, re.I)):
                    tag.decompose()

            # 按优先级寻找正文容器
            selectors = [
                {"role": "main"},
                {"itemprop": "articleBody"},
                "article",
                '[class*="article-body"]',
                '[class*="post-content"]',
                '[class*="entry-content"]',
                "main",
                '[class*="content"]',
                "#content",
                "#article",
                "#post",
            ]

            for selector in selectors:
                if isinstance(selector, dict):
                    found = soup.find(attrs=selector)
                else:
                    found = soup.select_one(selector)
                if found:
                    text = found.get_text(separator="\n", strip=True)
                    if len(text) > 200:
                        return text

            # 最后兜底: 取 body 文本
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
                # 清理多余空白行
                text = re.sub(r'\n{3,}', '\n\n', text)
                return text

            return ""
        except Exception as e:
            logger.debug(f"BeautifulSoup 提取失败: {e}")
            return ""

    # ── RSS抓取 ──────────────────────────────────────

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
                link = entry.get("link", "")

                if not title or not link:
                    continue

                # 提取RSS正文（content:encoded 优先）
                rss_content = self._extract_rss_content(entry)
                summary = entry.get("summary", entry.get("description", ""))
                summary_clean = self._clean_html(summary) if summary else ""

                # 判定是否需要抓取原文
                full_content = ""
                if self._is_substantial_content(rss_content):
                    # RSS已经提供了完整正文，直接使用
                    full_content = rss_content[:FULL_CONTENT_MAX_LENGTH]
                    logger.debug(f"采集Agent: [{name}] RSS正文 {len(full_content)} 字符 — {title[:40]}")
                elif FULL_CONTENT_FETCH_ENABLED:
                    # RSS只有摘要，去原文页面抓取正文
                    logger.debug(f"采集Agent: [{name}] RSS仅有摘要，抓取原文 — {title[:40]}")
                    full_content = self._fetch_full_article(link, rss_content or summary_clean)
                else:
                    full_content = rss_content or summary_clean

                # 简短摘要文本（用于关键词过滤等）
                text = f"{title}. {self._clean_html(summary)}" if summary else title

                published = self._parse_date(entry)

                articles.append(RawTweet(
                    tweet_id=self._make_id(name, link),
                    author=name,
                    author_username=source.get("username", name),
                    text=text[:500],
                    url=link,
                    published_at=published,
                    full_content=full_content,
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

            articles = self.fetch_rss(source)
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
             "TSMC's 3nm (N3) process technology has reached a significant milestone, with yields now exceeding 92% according to supply chain sources. This achievement surpasses the company's internal targets for the Q3 production ramp and positions TSMC to meet surging demand from major customers including Apple, NVIDIA, and AMD. The high yield rate is particularly noteworthy given the complexity of the N3 node, which represents TSMC's most advanced process currently in volume production. The N3 family includes N3B, N3E, N3P, and N3X variants, each optimized for different applications ranging from mobile SoCs to high-performance computing. This development puts increased pressure on Samsung Foundry, which has been struggling to achieve competitive yields on its 3nm Gate-All-Around (GAA) process. Industry analysts note that TSMC's yield advantage could translate into better performance-per-watt and lower defect densities for customers, further cementing TSMC's dominant position in the advanced foundry market.",
             "https://www.anandtech.com/tsmc-3nm-yield-92", "Foundry",
             now),
            ("NVIDIA Places Massive CoWoS Advanced Packaging Order Through 2026",
             "NVIDIA has reportedly secured a substantial portion of TSMC's CoWoS advanced packaging capacity through 2026, signaling continued explosive demand for its next-generation AI accelerators including Blackwell Ultra and Rubin.",
             "NVIDIA has reportedly secured a substantial portion of TSMC's CoWoS (Chip-on-Wafer-on-Substrate) advanced packaging capacity through 2026, according to multiple supply chain reports. This massive order signals continued explosive demand for NVIDIA's next-generation AI accelerators, including the Blackwell Ultra and upcoming Rubin architecture. The CoWoS packaging technology is critical for NVIDIA's high-end GPUs, as it enables the integration of compute dies with high-bandwidth memory (HBM) in a single package. TSMC has been rapidly expanding its CoWoS capacity, with plans to more than double output by the end of 2025. However, NVIDIA's aggressive booking suggests that supply constraints may persist well into 2026. The development also has implications for other AI chip makers, including AMD and Intel, who may face challenges securing sufficient advanced packaging capacity for their own AI accelerator products.",
             "https://www.tomshardware.com/nvidia-cowos-order-2026", "GPU",
             now),
            ("Intel Arrow Lake-S Benchmarks Leak: 15% Single-Thread Uplift",
             "Early benchmarks of Intel's Arrow Lake-S desktop processors show approximately 15% single-thread performance improvement over Raptor Lake, positioning it competitively against AMD's Zen 5 lineup in the upcoming desktop CPU battle.",
             "Early benchmark results for Intel's Arrow Lake-S desktop processors have leaked, showing approximately 15% single-thread performance improvement over the current Raptor Lake generation. The leaked benchmarks, which appeared on multiple hardware testing databases, suggest that Arrow Lake-S will be competitive against AMD's Zen 5-based Ryzen 9000 series in the upcoming desktop CPU battle. Arrow Lake represents Intel's first desktop platform to use a disaggregated tile-based architecture, combining a compute tile manufactured on Intel 20A process with graphics and I/O tiles from TSMC. The new Lion Cove P-cores and Skymont E-cores are expected to deliver significant IPC improvements while also improving power efficiency. The platform will also introduce the new LGA 1851 socket and support for DDR5-6400 memory. Intel is expected to officially launch Arrow Lake-S in the second half of 2024, with retail availability likely in Q4.",
             "https://wccftech.com/intel-arrow-lake-s-benchmark-leak", "CPU",
             now),
            ("AMD MI300X Availability Improves as Viable H100 Alternative",
             "AMD's MI300X datacenter GPU availability has significantly improved in Q2, with cloud providers reporting it as a cost-effective alternative to NVIDIA H100 for AI inference workloads, offering up to 30% lower TCO.",
             "AMD's MI300X datacenter GPU availability has significantly improved in Q2 2024, with multiple cloud providers now offering instances powered by the chip as a cost-effective alternative to NVIDIA's H100 for AI inference workloads. According to reports from cloud service providers and enterprise customers, the MI300X offers up to 30% lower total cost of ownership (TCO) for certain AI inference workloads compared to H100. The MI300X features 192GB of HBM3 memory with 5.3TB/s of bandwidth, giving it a memory capacity advantage over the H100's 80GB. This makes it particularly attractive for large language model inference, where memory capacity is often the bottleneck. AMD's ROCm software ecosystem has also matured significantly, with broader support for popular AI frameworks including PyTorch, TensorFlow, and JAX. While NVIDIA still dominates the AI training market, AMD's growing presence in the inference segment represents a meaningful shift in the competitive landscape.",
             "https://www.semianalysis.com/amd-mi300x-availability", "AI",
             now),
            ("China's SMIC Makes Progress on 5nm Using DUV Equipment",
             "SMIC reportedly making incremental progress on 5nm process development using existing DUV lithography equipment, though yields remain below commercially viable levels. Industry analysts remain cautious about production timeline.",
             "China's Semiconductor Manufacturing International Corporation (SMIC) is reportedly making incremental progress on 5nm process development using existing DUV (Deep Ultraviolet) lithography equipment, according to industry sources. The company has been forced to rely on DUV systems due to US export restrictions that prevent it from acquiring ASML's EUV (Extreme Ultraviolet) lithography machines. While SMIC has demonstrated the ability to produce 7nm-class chips using DUV multi-patterning techniques, advancing to 5nm presents significantly greater technical challenges. Current yields are believed to be below commercially viable levels, and industry analysts remain cautious about the production timeline. The development has important geopolitical implications, as China seeks to reduce its dependence on foreign semiconductor technology. However, even if SMIC achieves 5nm production capability, the cost and complexity of DUV-based 5nm manufacturing would likely make it economically uncompetitive compared to TSMC and Samsung's EUV-based processes.",
             "https://semiengineering.com/smic-5nm-duv-progress", "Foundry",
             now),
            ("Samsung HBM3E Memory Qualified for NVIDIA's Next-Gen GPUs",
             "Samsung Electronics has reportedly passed qualification for its HBM3E memory with NVIDIA, paving the way for supply into next-generation AI accelerators and easing the memory bottleneck in AI computing.",
             "Samsung Electronics has reportedly passed qualification testing for its HBM3E (High Bandwidth Memory 3E) memory with NVIDIA, marking a significant milestone for the Korean memory giant. The qualification paves the way for Samsung to supply HBM3E memory for NVIDIA's next-generation AI accelerators, including the B200 and potentially the upcoming Rubin architecture. This development is expected to ease the memory bottleneck that has constrained AI accelerator production, as Samsung joins SK Hynix as a qualified HBM3E supplier. Samsung's HBM3E offers data transfer rates of up to 9.8Gbps per pin and capacities of up to 36GB per stack. The company has been investing heavily in HBM production capacity, with plans to increase output by 2.5x in 2024 compared to the previous year. The qualification also has implications for Samsung's foundry business, as the company aims to offer a comprehensive HBM-plus-advanced-packaging solution to compete with TSMC's CoWoS ecosystem.",
             "https://www.semiconductor-digest.com/samsung-hbm3e-nvidia", "Semiconductor",
             now),
            ("Apple M4 Chip Details: TSMC N3E, 38 TOPS Neural Engine",
             "Apple's M4 processor, built on TSMC's N3E process, features an enhanced Neural Engine capable of 38 trillion operations per second (TOPS), positioning upcoming MacBooks as serious platforms for local AI development and inference.",
             "Apple's M4 processor, built on TSMC's N3E (3nm Enhanced) process technology, features a significantly enhanced Neural Engine capable of 38 trillion operations per second (TOPS), according to detailed specifications that have emerged from supply chain sources. This represents a substantial increase from the M3's 18 TOPS Neural Engine and positions upcoming MacBooks as serious platforms for local AI development and inference workloads. The M4 is expected to feature a new CPU core architecture with improved per-core performance and energy efficiency, along with a next-generation GPU that supports hardware-accelerated ray tracing and mesh shading. The chip's AI capabilities are particularly noteworthy given Apple's push into on-device AI with Apple Intelligence features announced at WWDC 2024. The M4 is expected to debut in updated MacBook Pro models later this year, followed by MacBook Air and iMac refreshes in early 2025. The chip's combination of performance, efficiency, and AI acceleration could make it a compelling platform for developers working on local LLM inference and other AI workloads.",
             "https://www.theverge.com/apple-m4-chip-details", "Digital",
             now),
            ("Global Semiconductor Market Forecast: 18% Growth in 2025",
             "Industry analysts project the global semiconductor market to grow 18% year-over-year in 2025, driven primarily by AI accelerator demand, memory market recovery, and increased adoption of advanced packaging technologies.",
             "Industry analysts project the global semiconductor market to grow 18% year-over-year in 2025, reaching approximately $720 billion in total revenue. The growth is being driven primarily by explosive demand for AI accelerators, a broad recovery in the memory market, and increased adoption of advanced packaging technologies. The AI chip segment alone is expected to exceed $150 billion in revenue, driven by continued investment in AI infrastructure by hyperscale cloud providers and enterprise customers. The memory market, which experienced a severe downturn in 2023, has rebounded strongly with HBM and DDR5 demand driving ASP increases. Geographically, the Americas and Asia-Pacific regions are expected to lead growth, while European semiconductor demand remains more modest. The forecast also highlights the growing importance of advanced packaging technologies like CoWoS, EMIB, and Foveros, which are becoming critical enablers for next-generation AI and HPC chips. However, analysts caution that geopolitical tensions and potential trade restrictions could introduce volatility into the market outlook.",
             "https://www.techspot.com/semiconductor-market-2025-forecast", "Semiconductor",
             now),
        ]

        articles = []
        for i, (title, text, full_content, url, cat, base_time) in enumerate(mock_articles):
            ts = (base_time - timedelta(hours=i * 2)).isoformat()
            articles.append(RawTweet(
                tweet_id=f"mock_{i}",
                author=f"TechSource-{i}",
                author_username=f"techsource{i}",
                text=f"{title}. {text}",
                url=url,
                published_at=ts,
                full_content=full_content,
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
        if t.full_content:
            print(f"    正文: {len(t.full_content)} 字符")
        print()