"""科技新闻聚合系统 - 数据模型"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class NewsCategory(str, Enum):
    GPU = "GPU"
    CPU = "CPU"
    AI = "AI"
    FOUNDRY = "Foundry"
    SEMICONDUCTOR = "Semiconductor"
    DIGITAL = "Digital"


@dataclass
class RawTweet:
    """采集Agent输出的原始推文"""
    tweet_id: str
    author: str
    author_username: str
    text: str
    url: str
    published_at: str         # ISO 8601
    metrics: dict = field(default_factory=dict)


@dataclass
class CleanedNews:
    """清洗Agent输出的去重+过滤后新闻"""
    tweet_id: str
    author: str
    author_username: str
    text: str
    url: str
    published_at: str


@dataclass
class StructuredNews:
    """总结Agent输出的结构化新闻 — 严格JSON模式"""
    id: str = field(default_factory=lambda: str(uuid4()))
    title: str = ""                    # AI生成的新闻标题
    summary: str = ""                  # 一句话摘要
    key_points: list[str] = field(default_factory=list)  # 关键要点，1-3条
    category: str = ""                 # GPU|CPU|AI|Foundry|Semiconductor|Digital
    source: str = ""                   # 原始账号名
    source_url: str = ""               # 原始推文链接
    original_author: str = ""          # 原账号 @handle
    published_at: str = ""             # ISO 8601
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def to_api_dict(self) -> dict:
        """返回给前端的精简格式"""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "key_points": self.key_points,
            "category": self.category,
            "source": self.source,
            "source_url": self.source_url,
            "original_author": self.original_author,
            "published_at": self.published_at,
            "created_at": self.created_at,
        }

    @staticmethod
    def json_schema() -> dict:
        """JSON Schema定义，确保输出一致性"""
        return {
            "type": "object",
            "required": [
                "id", "title", "summary", "key_points", "category",
                "source", "source_url", "original_author",
                "published_at", "created_at"
            ],
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "title": {"type": "string", "description": "AI生成的新闻标题"},
                "summary": {"type": "string", "description": "一句话摘要，不超过120字"},
                "key_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 3,
                    "description": "1-3条关键要点"
                },
                "category": {
                    "type": "string",
                    "enum": [c.value for c in NewsCategory],
                    "description": "新闻分类"
                },
                "source": {"type": "string", "description": "原始来源账号名"},
                "source_url": {"type": "string", "format": "uri", "description": "原始推文链接"},
                "original_author": {"type": "string", "description": "原账号 @handle"},
                "published_at": {"type": "string", "format": "date-time"},
                "created_at": {"type": "string", "format": "date-time"},
            }
        }