CREATE TABLE IF NOT EXISTS memory_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope text NOT NULL,
    scope_id text NOT NULL,
    key text NOT NULL,
    content jsonb NOT NULL,
    provenance jsonb DEFAULT '{}',
    safety_label text,
    created_at timestamptz DEFAULT now(),
    last_used_at timestamptz DEFAULT now(),
    UNIQUE(scope, scope_id, key)
);

CREATE INDEX IF NOT EXISTS idx_memory_scope_key ON memory_entries(scope, scope_id, key);
CREATE INDEX IF NOT EXISTS idx_memory_scope_prefix ON memory_entries(scope, scope_id, key text_pattern_ops);
