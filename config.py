"""科技新闻聚合系统 - 全局配置"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Supabase ────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ── DeepSeek / OpenAI ──────────────────────────────────
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ── 代理配置 ───────────────────────────────────────────
HTTP_PROXY = os.getenv("HTTP_PROXY", "")
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")

# ── 爬虫配置 ───────────────────────────────────────────
CRAWL_DELAY_MIN = float(os.getenv("CRAWL_DELAY_MIN", "3"))      # 最小请求间隔(秒)
CRAWL_DELAY_MAX = float(os.getenv("CRAWL_DELAY_MAX", "8"))      # 最大请求间隔(秒)
CRAWL_TIMEOUT = int(os.getenv("CRAWL_TIMEOUT", "30"))           # 请求超时(秒)
MAX_ARTICLES_PER_SOURCE = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "5"))
USER_AGENT = os.getenv("USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# ── 科技新闻源（RSS + 网页）───────────────────────────
NEWS_SOURCES: list[dict] = [
    # ── RSS源（轻量、不易被封） ──
    {
        "name": "Tom's Hardware",
        "type": "rss",
        "url": "https://www.tomshardware.com/feeds.xml",
        "category_hint": "GPU",
    },
    {
        "name": "WCCFTech",
        "type": "rss",
        "url": "https://wccftech.com/feed/",
        "category_hint": "GPU",
    },
    {
        "name": "SemiAnalysis",
        "type": "rss",
        "url": "https://semianalysis.com/feed/",
        "category_hint": "Semiconductor",
    },
    {
        "name": "Semiconductor Engineering",
        "type": "rss",
        "url": "https://semiengineering.com/feed/",
        "category_hint": "Semiconductor",
    },
    {
        "name": "Semiconductor Digest",
        "type": "rss",
        "url": "https://www.semiconductor-digest.com/feed/",
        "category_hint": "Semiconductor",
    },
    {
        "name": "Ars Technica (Gadgets)",
        "type": "rss",
        "url": "https://feeds.arstechnica.com/arstechnica/gadgets",
        "category_hint": "Digital",
    },
    # ── 中文源 ──
    {
        "name": "IT之家",
        "type": "rss",
        "url": "https://www.ithome.com/rss/",
        "category_hint": "Digital",
    },
    {
        "name": "Solidot (科技)",
        "type": "rss",
        "url": "https://www.solidot.org/index.rss",
        "category_hint": "Semiconductor",
    },
]

# ── 科技关键词过滤器 ──────────────────────────────────
TECH_KEYWORDS: list[str] = [
    # GPU
    "GPU", "NVIDIA", "GeForce", "显卡", "H100", "B200", "RTX", "Radeon", "CUDA",
    "Blackwell", "Hopper", "图形处理器", "MI300", "MI400", "Instinct",
    # CPU
    "CPU", "Intel", "处理器", "Xeon", "EPYC", "酷睿", "锐龙", "Ryzen",
    "Arrow Lake", "Granite Rapids", "Zen", "Lunar Lake", "Core Ultra",
    # AI
    "AI", "大模型", "LLM", "GPT", "DeepSeek", "OpenAI", "Claude", "Gemini",
    "Copilot", "机器学习", "深度学习", "神经网络", "transformer",
    "AGI", "训练", "推理", "NPU", "TPU", "人工智能",
    # Foundry / 半导体制造
    "台积电", "TSMC", "三星", "Intel Foundry", "中芯国际",
    "SMIC", "EUV", "光刻", "3nm", "2nm", "5nm", "7nm", "wafer",
    "晶圆", "制程", "FinFET", "GAA", "先进封装", "CoWoS",
    "HBM", "DRAM", "NAND", "存储", "美光", "SK海力士", "Micron",
    # 半导体
    "半导体", "芯片", "chip", "semiconductor", "ARM", "高通", "Qualcomm",
    "联发科", "MediaTek", "博通", "Broadcom", "ASML", "应用材料",
    "Silicon", "RISC-V",
    # 数码
    "数码", "手机", "iPhone", "iPad", "MacBook", "Pixel", "折叠屏",
    "智能穿戴", "AR", "VR", "Vision Pro", "消费电子",
]