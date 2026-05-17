-- =============================================================================
-- FSH COMMAND CENTER — PILLAR EXTENSION TABLES
-- Version: 1.0.0
-- =============================================================================

-- ---------------------------------------------------------------------------
-- COMMERCE — products
-- ---------------------------------------------------------------------------
CREATE TABLE commerce_products (
    product_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    price           NUMERIC(12,2),
    currency        TEXT DEFAULT 'USD',
    stock_level     INT DEFAULT 0,
    platform        TEXT NOT NULL, -- e.g., 'amazon', 'tiktok', 'shopify'
    external_id     TEXT,          -- platform-specific ID
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_commerce_products_updated_at
    BEFORE UPDATE ON commerce_products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- CONTENT — audience & engagement
-- ---------------------------------------------------------------------------
CREATE TABLE content_audience (
    audience_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id         UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
    platform        TEXT NOT NULL, -- e.g., 'tiktok', 'instagram', 'youtube'
    handle          TEXT NOT NULL,
    follower_count  INT DEFAULT 0,
    engagement_rate NUMERIC(5,4),
    last_scraped_at TIMESTAMPTZ,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_content_audience_updated_at
    BEFORE UPDATE ON content_audience
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- FORGE — sops & blueprints
-- ---------------------------------------------------------------------------
CREATE TABLE forge_sops (
    sop_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1.0.0',
    content_markdown TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'review', 'published', 'deprecated')),
    author          TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_forge_sops_updated_at
    BEFORE UPDATE ON forge_sops
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- TRADING — signals (already in core, but adding execution log)
-- ---------------------------------------------------------------------------
CREATE TABLE trading_executions (
    execution_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    signal_id       UUID REFERENCES trading_signals(signal_id) ON DELETE CASCADE,
    task_id         UUID REFERENCES tasks(task_id) ON DELETE SET NULL,
    ticker          TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    quantity        NUMERIC(18,8) NOT NULL,
    price           NUMERIC(18,8) NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'filled', 'cancelled', 'failed')),
    external_ref    TEXT, -- broker order ID
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- CROSS-PILLAR FLYWHEEL VIEWS
-- ---------------------------------------------------------------------------

-- View to link Gridline leads to Content engagement
CREATE VIEW v_lead_content_flywheel AS
SELECT l.lead_id, l.address, l.owner_name, l.lead_score,
       a.platform, a.handle, a.engagement_rate
FROM gridline_leads l
JOIN tasks t ON t.objective ILIKE '%' || l.address || '%'
JOIN content_audience a ON a.task_id = t.task_id
WHERE l.pillar = 'gridline';
