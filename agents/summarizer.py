"""总结Agent — 调用LLM生成结构化新闻摘要

输入: CleanedNews列表
输出: StructuredNews列表（严格JSON格式）

要求:
- 不允许复制原文
- 必须提供source_url
- 必须分类到GPU/CPU/AI/Foundry/Semiconductor/Digital
"""

import json
import logging
from openai import OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from models import CleanedNews, StructuredNews, NewsCategory

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的科技新闻编辑。你的任务是将原始推文/文章内容转化为结构化的中文科技新闻摘要。

## 核心规则
1. **禁止复制原文** — 必须用自己的语言重新概括，不得逐字复制原文。
2. **必须保留source_url** — 每条输出的source_url必须与输入一致。
3. **全部使用中文输出** — title、summary、key_points 三个字段都必须用中文撰写。
4. **准确分类** — 从以下类别中选择最匹配的一个:
   - GPU — 显卡、GPU架构、游戏显卡、数据中心GPU
   - CPU — 处理器、CPU架构、服务器CPU、桌面/移动CPU
   - AI — 人工智能、大模型、AI应用、机器学习、NPU/TPU
   - Foundry — 芯片制造、晶圆代工、制程工艺、先进封装
   - Semiconductor — 半导体产业、存储芯片、芯片设计、EDA、设备材料
   - Digital — 消费电子、手机、PC、智能穿戴、AR/VR

5. **输出格式** — 严格的JSON数组，每条新闻对象包含:
   - title: 简洁的新闻标题（中文，不超过30字）
   - summary: 一句话摘要（中文，不超过120字）
   - key_points: 1-3条关键要点（中文），每条不超过40字
   - category: 分类标签
   - source: 来源账号名（不含@）
   - source_url: 原始链接（必须与输入的url一致）
   - original_author: 原始作者@handle

## 输出示例
```json
[
  {
    "title": "台积电3nm良率达92%超预期",
    "summary": "台积电N3工艺良率据报道已达92%，超出Q3生产爬坡预期，对三星代工业务形成竞争压力。",
    "key_points": ["N3良率达92%", "超越Q3预期", "三星面临竞争压力"],
    "category": "Foundry",
    "source": "SemiconductorTF",
    "source_url": "https://x.com/SemiconductorTF/status/1",
    "original_author": "@SemiconductorTF"
  }
]
```

请为每条输入新闻生成一条对应的结构化输出，严格保持输入顺序。"""


class NewsSummarizer:
    """AI新闻总结器"""

    def __init__(self):
        self._available = bool(LLM_API_KEY)
        self.model = LLM_MODEL
        if self._available:
            self.client = OpenAI(
                api_key=LLM_API_KEY,
                base_url=LLM_BASE_URL,
            )
        else:
            self.client = None

    def _build_user_message(self, news_list: list[CleanedNews]) -> str:
        """构建发送给LLM的用户消息"""
        items = []
        for i, news in enumerate(news_list):
            items.append(
                f"[{i}]\n"
                f"  source: {news.author}\n"
                f"  author: @{news.author_username}\n"
                f"  url: {news.url}\n"
                f"  text: {news.text}\n"
                f"  published_at: {news.published_at}"
            )
        return "请将以下推文转化为结构化新闻摘要:\n\n" + "\n\n".join(items)

    def _parse_response(self, response_text: str, input_news: list[CleanedNews]) -> list[StructuredNews]:
        """解析LLM响应为StructuredNews列表"""
        try:
            # 尝试提取JSON数组
            json_start = response_text.find("[")
            json_end = response_text.rfind("]") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                items = json.loads(json_str)
            else:
                items = json.loads(response_text)
        except json.JSONDecodeError:
            logger.error(f"LLM响应JSON解析失败: {response_text[:300]}")
            return self._fallback_summarize(input_news)

        results = []
        valid_categories = {c.value for c in NewsCategory}
        for i, item in enumerate(items):
            category = item.get("category", "AI")
            if category not in valid_categories:
                category = "AI"

            news = StructuredNews(
                title=item.get("title", "")[:100],
                summary=item.get("summary", "")[:200],
                key_points=item.get("key_points", [])[:3],
                category=category,
                source=item.get("source", ""),
                source_url=item.get("source_url", ""),
                original_author=item.get("original_author", ""),
                published_at=input_news[i].published_at if i < len(input_news) else "",
            )
            results.append(news)

        return results

    def summarize(self, news_list: list[CleanedNews]) -> list[StructuredNews]:
        """对清洗后的新闻进行LLM总结"""
        if not news_list:
            return []

        if not self._available:
            logger.warning("总结Agent: LLM API Key未配置，使用规则总结")
            return self._fallback_summarize(news_list)

        user_msg = self._build_user_message(news_list)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            content = resp.choices[0].message.content or ""
            logger.info(f"总结Agent: LLM返回 {len(content)} 字符")
            return self._parse_response(content, news_list)
        except Exception as e:
            logger.error(f"总结Agent: LLM调用失败 — {e}")
            return self._fallback_summarize(news_list)

    def _fallback_summarize(self, news_list: list[CleanedNews]) -> list[StructuredNews]:
        """无LLM时的规则化兜底总结"""
        import re
        results = []
        for news in news_list:
            text = news.text

            # 简单分类
            text_lower = text.lower()
            if any(w in text_lower for w in ["gpu", "nvidia", "h100", "b200", "rtx", "显卡"]):
                category = "GPU"
            elif any(w in text_lower for w in ["cpu", "intel", "amd", "处理器", "xeon", "epyc"]):
                category = "CPU"
            elif any(w in text_lower for w in ["ai", "gpt", "llm", "大模型", "deepseek"]):
                category = "AI"
            elif any(w in text_lower for w in ["tsmc", "台积电", "samsung", "3nm", "2nm", "晶圆", "foundry", "制程"]):
                category = "Foundry"
            elif any(w in text_lower for w in ["半导体", "chip", "semiconductor", "hbm", "dram", "nand"]):
                category = "Semiconductor"
            else:
                category = "Digital"

            # 用首句做标题
            sentences = re.split(r'[.。!！?？\n]', text)
            first_sentence = sentences[0].strip()[:80] if sentences else text[:80]
            title = first_sentence[:30] + ("..." if len(first_sentence) > 30 else "")

            # 摘要
            summary = text[:120] + ("..." if len(text) > 120 else "")

            results.append(StructuredNews(
                title=title,
                summary=summary,
                key_points=[text[:40]],
                category=category,
                source=news.author,
                source_url=news.url,
                original_author=news.author_username,
                published_at=news.published_at,
            ))
        logger.info(f"总结Agent(兜底): 处理 {len(results)} 条新闻")
        return results


def run_summarizer(news_list: list[CleanedNews]) -> list[StructuredNews]:
    """总结Agent入口"""
    summarizer = NewsSummarizer()
    return summarizer.summarize(news_list)