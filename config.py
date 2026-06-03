"""科技新闻聚合系统 - 全局配置"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Supabase ────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # service_role key

# ── X (Twitter) API ────────────────────────────────────
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
X_API_KEY = os.getenv("X_API_KEY", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")

# ── DeepSeek / OpenAI ──────────────────────────────────
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ── 目标X账号（科技领域）──────────────────────────────
TARGET_ACCOUNTS: list[str] = [
    "SemiconductorTF",   # 半导体
    "dan_nystedt",       # 芯片/半导体
    "TechInsights",      # 芯片拆解分析
    "anshelsag",         # 芯片/AI分析师
    "DrIanCutress",      # CPU/GPU分析师
    "Kurnal",            # 半导体/GPU
    "Sino_Market",       # 中国科技市场
    "IC_insights",       # 集成电路
    "KoreaChip",         # 韩国半导体
]

# ── 科技关键词过滤器 ──────────────────────────────────
TECH_KEYWORDS: list[str] = [
    # GPU
    "GPU", "NVIDIA", "AMD", "显卡", "H100", "B200", "RTX", "Radeon", "CUDA",
    "Blackwell", "Hopper", "图形处理器",
    # CPU
    "CPU", "Intel", "AMD", "处理器", "Xeon", "EPYC", "酷睿", "锐龙",
    "Arrow Lake", "Granite Rapids", "Zen", "Lunar Lake",
    # AI
    "AI", "大模型", "LLM", "GPT", "DeepSeek", "OpenAI", "Claude", "Gemini",
    "Copilot", "机器学习", "深度学习", "神经网络", "transformer",
    "AGI", "训练", "推理", "NPU", "TPU", "人工智能",
    # Foundry / 半导体制造
    "台积电", "TSMC", "三星", "Samsung", "Intel Foundry", "中芯国际",
    "SMIC", "EUV", "光刻", "3nm", "2nm", "5nm", "7nm", "wafer",
    "晶圆", "制程", "FinFET", "GAA", "先进封装", "CoWoS",
    "HBM", "DRAM", "NAND", "存储", "美光", "SK海力士", "Micron",
    # 半导体
    "半导体", "芯片", "chip", "semiconductor", "ARM", "高通", "Qualcomm",
    "联发科", "MediaTek", "博通", "Broadcom", "ASML", "应用材料",
    # 数码
    "数码", "手机", "iPhone", "iPad", "MacBook", "Pixel", "折叠屏",
    "智能穿戴", "AR", "VR", "Vision Pro", "消费电子",
]

# ── 采集配置 ──────────────────────────────────────────
MAX_TWEETS_PER_ACCOUNT = 10
REQUEST_DELAY_SECONDS = 2