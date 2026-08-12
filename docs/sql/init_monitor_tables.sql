CREATE TABLE IF NOT EXISTS monitor_rules (
    rule_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    rule_type TEXT NOT NULL CHECK (rule_type IN ('address_transaction', 'large_transaction')),
    address TEXT,
    min_amount NUMERIC,
    min_amount_usd NUMERIC,
    chain TEXT,
    token TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    notification_channel TEXT NOT NULL DEFAULT 'feishu',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_triggered_at TIMESTAMPTZ,
    CHECK (address IS NOT NULL OR min_amount IS NOT NULL OR min_amount_usd IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_monitor_rules_user ON monitor_rules(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_monitor_rules_enabled ON monitor_rules(enabled) WHERE enabled;

CREATE TABLE IF NOT EXISTS monitor_notification_events (
    event_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES monitor_rules(rule_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    transaction_hash TEXT NOT NULL,
    channel TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    UNIQUE(rule_id, transaction_id)
);
CREATE INDEX IF NOT EXISTS idx_monitor_notification_pending
    ON monitor_notification_events(status, created_at);

CREATE TABLE IF NOT EXISTS monitor_scan_state (
    worker_name TEXT PRIMARY KEY,
    last_processed_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS monitor_notification_configs (
    user_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id, channel)
);
