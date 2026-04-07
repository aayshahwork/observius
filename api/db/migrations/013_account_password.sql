-- Migration 013: Add password_hash to accounts for email+password auth
ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);
