-- 科技新闻聚合系统 - Supabase 数据库Schema

-- 启用UUID扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 新闻主表
CREATE TABLE IF NOT EXISTS tech_news (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,                          -- AI生成的标题
    summary         TEXT NOT NULL,                          -- 一句话摘要
    key_points      JSONB NOT NULL DEFAULT '[]'::jsonb,     -- 关键要点数组
    category        TEXT NOT NULL CHECK (category IN (
                        'GPU', 'CPU', 'AI', 'Foundry',
                        'Semiconductor', 'Digital'
                    )),
    source          TEXT NOT NULL,                          -- 来源账号名
    source_url      TEXT NOT NULL UNIQUE,                   -- 原始链接（去重键）
    original_author TEXT NOT NULL,                          -- @handle
    published_at    TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_news_category      ON tech_news (category);
CREATE INDEX IF NOT EXISTS idx_news_published_at  ON tech_news (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_created_at    ON tech_news (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_source_url    ON tech_news (source_url);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_source_url_unique ON tech_news (source_url);

-- 全文搜索索引（中文需要pg_jieba或zhparser，这里用简单索引）
CREATE INDEX IF NOT EXISTS idx_news_search ON tech_news
    USING GIN (to_tsvector('simple', title || ' ' || summary));

-- 自动更新 updated_at
CREATE OR REPLACE FUNCTION update_modified_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_news_updated_at ON tech_news;
CREATE TRIGGER trg_news_updated_at
    BEFORE UPDATE ON tech_news
    FOR EACH ROW EXECUTE FUNCTION update_modified_column();

-- 去重记录表（记录已处理过的tweet_id）
CREATE TABLE IF NOT EXISTS processed_tweets (
    tweet_id        TEXT PRIMARY KEY,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Row Level Security (Supabase)
ALTER TABLE tech_news ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_tweets ENABLE ROW LEVEL SECURITY;

-- 允许匿名读取新闻
DROP POLICY IF EXISTS "Allow public read" ON tech_news;
CREATE POLICY "Allow public read" ON tech_news
    FOR SELECT USING (true);

-- 仅service_role可写入
DROP POLICY IF EXISTS "Allow service write" ON tech_news;
CREATE POLICY "Allow service write" ON tech_news
    FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow service update" ON tech_news
    FOR UPDATE USING (true);
CREATE POLICY "Allow service delete" ON tech_news
    FOR DELETE USING (true);

-- API: 最近新闻查询（按分类筛选+分页）
-- 示例查询:
-- SELECT * FROM tech_news
-- WHERE category = 'AI'
-- ORDER BY published_at DESC
-- LIMIT 20 OFFSET 0;