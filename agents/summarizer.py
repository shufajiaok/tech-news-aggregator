"""总结Agent — 调用LLM生成结构化新闻摘要

输入: CleanedNews列表（含full_content正文）
输出: StructuredNews列表（严格JSON格式）

要求:
- 必须基于full_content生成AI总结（300-500字）
- 不允许复制原文
- 必须提供source_url
- 必须分类到GPU/CPU/AI/Foundry/Semiconductor/Digital
- 正文抓取失败时降级使用RSS摘要
"""

import json
import logging
from openai import OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from models import CleanedNews, StructuredNews, NewsCategory

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的科技新闻编辑，专注于半导体、芯片、AI、数码领域。你的任务是将原始新闻正文转化为结构化的中文科技新闻摘要。

## 最重要规则：必须全部使用中文
**无论输入是英文还是中文，你输出的所有文字内容都必须使用中文。**
- title 必须是中文标题
- summary 必须是中文摘要
- key_points 必须是中文要点
- ai_summary 必须是中文深度总结
- 英文品牌名称（如 NVIDIA、TSMC）保持原名，但描述性文字全用中文
- 违反此规则是不允许的

## 核心规则
1. **禁止复制原文** — 必须用自己的语言重新概括，不得逐字复制原文。
2. **必须基于full_content生成** — 当提供了full_content时，必须基于完整正文生成总结，不能仅依据RSS摘要。
3. **必须保留source_url** — 每条输出的source_url必须与输入一致。
4. **准确分类** — 从以下类别中选择最匹配的一个:
   - GPU — 显卡、GPU架构、游戏显卡、数据中心GPU
   - CPU — 处理器、CPU架构、服务器CPU、桌面/移动CPU
   - AI — 人工智能、大模型、AI应用、机器学习、NPU/TPU
   - Foundry — 芯片制造、晶圆代工、制程工艺、先进封装
   - Semiconductor — 半导体产业、存储芯片、芯片设计、EDA、设备材料
   - Digital — 消费电子、手机、PC、智能穿戴、AR/VR

## AI总结要求（ai_summary字段）
- **长度**: 300-500字之间
- **结构**: 必须包含以下三个部分，每部分用自然段落形式呈现:
  1. **发生了什么** — 事件本身的核心事实和数据
  2. **为什么重要** — 事件的背景、意义、对行业格局的影响
  3. **对行业有什么影响** — 前瞻性分析，对产业链、竞争格局、消费者的影响
- **风格**: 专业、客观、有深度，让读者不需要看原文就能理解新闻全貌

## 输出格式
严格的JSON数组，每条新闻对象包含:
  - title: 简洁的新闻标题（中文，不超过30字）
  - summary: 一句话摘要（中文，不超过120字）
  - key_points: 1-3条关键要点（中文），每条不超过40字
  - ai_summary: 深度总结（中文，300-500字），含"发生了什么/为什么重要/对行业有什么影响"三部分
  - category: 分类标签
  - source: 来源账号名（不含@）
  - source_url: 原始链接（必须与输入的url一致）
  - original_author: 原始作者@handle

## 输出示例
```json
[
  {
    "title": "NVIDIA发布B200 GPU，AI训练性能提升4倍",
    "summary": "NVIDIA正式发布B200 GPU，搭载192GB HBM3e显存，AI训练性能达到H100的4倍，预计2024年下半年出货。",
    "key_points": ["B200 GPU配备192GB HBM3e", "AI训练性能是H100的4倍", "预计2024年下半年出货"],
    "ai_sumannary": "NVIDIA在GTC 2024大会上正式发布了新一代Blackwell架构的B200 GPU，这是继Hopper架构之后NVIDIA在AI加速器领域的又一次重大升级。\n\n发生了什么：B200采用台积电4nm工艺，配备192GB HBM3e高带宽显存，内存带宽达到8TB/s。在AI训练场景下，B200的单卡性能达到H100的4倍，而在大模型推理场景下，性能提升更是高达30倍。这主要得益于Blackwell架构引入的第二代Transformer引擎和FP4精度支持。NVIDIA同时发布了由两颗B200和一颗Grace CPU组成的GB200超级芯片，以及将36颗GB200互联的GB200 NVL72机架级系统。\n\n为什么重要：B200的发布进一步巩固了NVIDIA在AI加速器市场的绝对领先地位。在AI大模型训练需求持续爆发的背景下，B200的性能飞跃意味着AI公司可以用更少的GPU、更低的能耗完成模型训练。192GB的显存容量也使得单卡即可运行万亿参数级别的大模型，大幅降低了推理部署的复杂度。\n\n对行业有什么影响：首先，B200将进一步拉大NVIDIA与竞争对手（AMD MI300X、Intel Gaudi 3）的性能差距，短期内NVIDIA的垄断地位难以撼动。其次，GB200 NVL72机架级系统的推出标志着AI计算正向更大规模集群化方向发展，这对数据中心的基础设施（供电、散热）提出了新的挑战。最后，B200的量产将依赖于台积电CoWoS-L先进封装产能，这可能会进一步加剧先进封装产能的紧张局面，影响整个半导体供应链。",
    "category": "GPU",
    "source": "NVIDIA",
    "source_url": "https://x.com/nvidia/status/1",
    "original_author": "@nvidia"
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
        """构建发送给LLM的用户消息。

        优先使用full_content作为正文，如果full_content为空则降级为text（RSS摘要）。
        """
        items = []
        for i, news in enumerate(news_list):
            # 优先使用完整正文，降级为摘要
            content = news.full_content if news.full_content else news.text
            content_label = "full_content" if news.full_content else "summary（正文抓取失败，降级）"

            items.append(
                f"[{i}]\n"
                f"  source: {news.author}\n"
                f"  author: @{news.author_username}\n"
                f"  url: {news.url}\n"
                f"  content_type: {content_label}\n"
                f"  content: {content}\n"
                f"  published_at: {news.published_at}"
            )
        return "请将以下新闻转化为结构化新闻摘要:\n\n" + "\n\n".join(items)

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
                full_content=input_news[i].full_content if i < len(input_news) else "",
                ai_summary=item.get("ai_summary", ""),
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

        # 统计有正文的新闻数量
        with_content = sum(1 for n in news_list if n.full_content)
        logger.info(f"总结Agent: {len(news_list)} 条新闻，其中 {with_content} 条有完整正文")

        user_msg = self._build_user_message(news_list)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.3,
                max_tokens=16384,
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
            text = news.full_content or news.text

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

            # 兜底AI总结（取正文前500字，标注为规则生成）
            fallback_ai = f"（⚠️ AI总结未生成，以下为规则兜底）\n\n{text[:500]}"

            results.append(StructuredNews(
                title=title,
                summary=summary,
                key_points=[text[:40]],
                full_content=news.full_content,
                ai_summary=fallback_ai,
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