-- 科技新闻聚合系统 - Supabase 数据库Schema (幂等版本)
-- 可安全重复执行，不会因重复创建而报错

-- 启用UUID扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 1. 新闻主表
-- ============================================================
CREATE TABLE IF NOT EXISTS tech_news (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    key_points      JSONB NOT NULL DEFAULT '[]'::jsonb,
    full_content    TEXT NOT NULL DEFAULT '',
    ai_summary      TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL CHECK (category IN (
                        'GPU', 'CPU', 'AI', 'Foundry',
                        'Semiconductor', 'Digital'
                    )),
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    original_author TEXT NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 索引（幂等）
CREATE INDEX IF NOT EXISTS idx_news_category      ON tech_news (category);
CREATE INDEX IF NOT EXISTS idx_news_published_at  ON tech_news (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_created_at    ON tech_news (created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_source_url_unique ON tech_news (source_url);

-- 迁移：为已有表添加新列（幂等）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tech_news' AND column_name = 'full_content'
    ) THEN
        ALTER TABLE tech_news ADD COLUMN full_content TEXT NOT NULL DEFAULT '';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'tech_news' AND column_name = 'ai_summary'
    ) THEN
        ALTER TABLE tech_news ADD COLUMN ai_summary TEXT NOT NULL DEFAULT '';
    END IF;
END $$;

-- ============================================================
-- 2. 去重记录表
-- ============================================================
CREATE TABLE IF NOT EXISTS processed_tweets (
    tweet_id        TEXT PRIMARY KEY,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 3. 自动更新 updated_at
-- ============================================================
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

-- ============================================================
-- 4. Row Level Security（先清理再创建）
-- ============================================================
ALTER TABLE tech_news ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_tweets ENABLE ROW LEVEL SECURITY;

-- 删除旧策略（幂等）
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow public read' AND tablename = 'tech_news') THEN
        DROP POLICY "Allow public read" ON tech_news;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service write' AND tablename = 'tech_news') THEN
        DROP POLICY "Allow service write" ON tech_news;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service update' AND tablename = 'tech_news') THEN
        DROP POLICY "Allow service update" ON tech_news;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Allow service delete' AND tablename = 'tech_news') THEN
        DROP POLICY "Allow service delete" ON tech_news;
    END IF;
END $$;

-- 创建策略
CREATE POLICY "Allow public read" ON tech_news
    FOR SELECT USING (true);

CREATE POLICY "Allow service write" ON tech_news
    FOR INSERT WITH CHECK (true);

CREATE POLICY "Allow service update" ON tech_news
    FOR UPDATE USING (true);

CREATE POLICY "Allow service delete" ON tech_news
    FOR DELETE USING (true);