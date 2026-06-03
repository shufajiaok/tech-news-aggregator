"""科技新闻聚合系统 — 主调度器

编排4个Agent的流水线:
  采集Agent → 清洗Agent → 总结Agent → 存储Agent

用法:
  python main.py              # 运行一次采集+处理
  python main.py --schedule   # 启动定时调度（每6小时）
"""

import logging
import time
import argparse
from datetime import datetime, timezone

from agents.collector import run_collector
from agents.cleaner import run_cleaner
from agents.summarizer import run_summarizer
from agents.storage import run_storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def run_pipeline():
    """运行一次完整的采集-处理流水线"""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info(f"科技新闻聚合流水线启动 — {start_time.isoformat()}")
    logger.info("=" * 60)

    # ── Step 1: 采集Agent ──────────────────────────
    logger.info("[1/4] 采集Agent: 开始获取X平台数据...")
    raw_tweets = run_collector()
    if not raw_tweets:
        logger.warning("采集Agent: 未获取到任何推文，流水线终止")
        return
    logger.info(f"[1/4] 采集Agent: 获取 {len(raw_tweets)} 条原始推文 ✓")

    # ── Step 2: 清洗Agent ──────────────────────────
    logger.info("[2/4] 清洗Agent: 开始过滤+去重...")
    cleaned = run_cleaner(raw_tweets)
    logger.info(f"[2/4] 清洗Agent: 过滤去重后保留 {len(cleaned)} 条 ✓")

    if not cleaned:
        logger.info("清洗Agent: 无科技相关新闻，流水线终止")
        return

    # ── Step 3: 总结Agent ──────────────────────────
    logger.info("[3/4] 总结Agent: 开始LLM结构化摘要...")
    structured = run_summarizer(cleaned)
    logger.info(f"[3/4] 总结Agent: 生成 {len(structured)} 条结构化新闻 ✓")

    # ── Step 4: 存储Agent ──────────────────────────
    logger.info("[4/4] 存储Agent: 开始写入数据库...")
    saved = run_storage(structured)
    logger.info(f"[4/4] 存储Agent: 成功写入 {saved}/{len(structured)} 条 ✓")

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info(f"流水线完成! 耗时 {elapsed:.1f}s | 采集{len(raw_tweets)} → 清洗{len(cleaned)} → 总结{len(structured)} → 存储{saved}")
    logger.info("=" * 60)


def run_scheduled(interval_hours: int = 6):
    """定时调度模式"""
    logger.info(f"定时调度启动: 每 {interval_hours} 小时执行一次")

    run_pipeline()  # 立即执行一次

    while True:
        next_run = time.time() + interval_hours * 3600
        logger.info(f"下一次执行: {datetime.fromtimestamp(next_run).strftime('%Y-%m-%d %H:%M:%S')}")
        time.sleep(interval_hours * 3600)
        run_pipeline()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="科技新闻聚合系统")
    parser.add_argument(
        "--schedule", "-s",
        action="store_true",
        help="启动定时调度模式（每6小时执行一次）",
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=6,
        help="调度间隔（小时），默认6小时",
    )
    args = parser.parse_args()

    if args.schedule:
        run_scheduled(args.interval)
    else:
        run_pipeline()