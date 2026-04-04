-- 002_seed_data.sql
-- Seed data: test account and API key for development.

BEGIN;

INSERT INTO accounts (id, email, name, tier, monthly_step_limit, encryption_key_id)
VALUES (
    uuid_generate_v7(),
    'test@pokant.dev',
    'Test Account',
    'free',
    500,
    'enc_key_test_001'
);

-- API key: cu_test_testkey1234567890abcdef12
-- Hash using SHA-256: sha256('cu_test_testkey1234567890abcdef12')
INSERT INTO api_keys (id, account_id, key_hash, key_prefix, key_suffix, label)
VALUES (
    uuid_generate_v7(),
    (SELECT id FROM accounts WHERE email = 'test@pokant.dev'),
    encode(sha256('cu_test_testkey1234567890abcdef12'::bytea), 'hex'),
    'cu_test_',
    'f12',
    'Default test key'
);

COMMIT;
