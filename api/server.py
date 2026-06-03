"""科技新闻聚合系统 — FastAPI接口服务

提供:
- GET  /api/news         新闻列表（分页+分类筛选）
- GET  /api/news/{id}    新闻详情
- GET  /api/categories   分类统计
- GET  /api/health       健康检查

启动:
  uvicorn api.server:app --reload --port 8000
"""

import logging
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agents.storage import get_storage

logger = logging.getLogger("api")

app = FastAPI(
    title="Tech News Aggregator API",
    description="科技新闻聚合系统 — X平台科技新闻结构化摘要",
    version="1.0.0",
)

# CORS（允许前端跨域访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    """健康检查"""
    return {"status": "ok", "service": "tech-news-aggregator"}


@app.get("/api/news")
def list_news(
    category: str | None = Query(None, description="分类筛选: GPU|CPU|AI|Foundry|Semiconductor|Digital"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """获取新闻列表"""
    storage = get_storage()
    valid_categories = {"GPU", "CPU", "AI", "Foundry", "Semiconductor", "Digital"}

    if category and category not in valid_categories:
        raise HTTPException(
            status_code=400,
            detail=f"无效分类: {category}。有效值: {', '.join(sorted(valid_categories))}",
        )

    news_list = storage.query_recent(category=category, limit=limit, offset=offset)
    return {
        "data": news_list,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "count": len(news_list),
        },
    }


@app.get("/api/news/{news_id}")
def get_news(news_id: str):
    """获取新闻详情"""
    storage = get_storage()
    news = storage.query_by_id(news_id)
    if not news:
        raise HTTPException(status_code=404, detail="新闻不存在")
    return {"data": news}


@app.get("/api/categories")
def list_categories():
    """获取分类统计"""
    storage = get_storage()
    counts = storage.get_categories()
    categories = [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)
    ]
    return {"data": categories}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)