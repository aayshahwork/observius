-- 005_accounts_webhook_secret.sql
-- Adds webhook_secret column to accounts for HMAC-SHA256 webhook signing.

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS webhook_secret VARCHAR(64);
