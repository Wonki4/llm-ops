-- =============================================================================
-- Seed script: mega-team with 30,000 members and 90,000 keys
-- Usage: docker exec -i litellm_db psql -U llmproxy -d litellm < scripts/seed_mega_team.sql
-- =============================================================================

BEGIN;

-- 1. Create the mega-team
INSERT INTO "LiteLLM_TeamTable" (
    team_id, team_alias, max_budget, spend, models,
    members, admins,
    members_with_roles, metadata, model_spend, model_max_budget
)
VALUES (
    'mega-team-001',
    'mega-team',
    50000.0,
    0,
    ARRAY['my-special-fake-model-alias-name'],
    -- members: admin001 + user00001..user30000
    ARRAY['admin001'] || (SELECT array_agg('user' || lpad(n::text, 5, '0')) FROM generate_series(1, 30000) AS n),
    ARRAY['admin001'],
    '[]'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
)
ON CONFLICT (team_id) DO UPDATE SET
    members = EXCLUDED.members,
    admins = EXCLUDED.admins,
    max_budget = EXCLUDED.max_budget,
    models = EXCLUDED.models;

-- 2. Create 30,000 users
INSERT INTO "LiteLLM_UserTable" (
    user_id, teams, spend, metadata, model_spend, model_max_budget
)
SELECT
    'user' || lpad(n::text, 5, '0'),
    ARRAY['mega-team-001'],
    0,
    '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
FROM generate_series(1, 30000) AS n
ON CONFLICT (user_id) DO UPDATE SET
    teams = "LiteLLM_UserTable".teams || ARRAY['mega-team-001'];

-- 3. Ensure admin001 is also a member (update teams array)
UPDATE "LiteLLM_UserTable"
SET teams = CASE
    WHEN NOT ('mega-team-001' = ANY(teams)) THEN teams || ARRAY['mega-team-001']
    ELSE teams
END
WHERE user_id = 'admin001';

-- 4. Create TeamMembership records (30,000 users + admin001)
INSERT INTO "LiteLLM_TeamMembership" (user_id, team_id, spend)
SELECT
    'user' || lpad(n::text, 5, '0'),
    'mega-team-001',
    0
FROM generate_series(1, 30000) AS n
ON CONFLICT (user_id, team_id) DO NOTHING;

INSERT INTO "LiteLLM_TeamMembership" (user_id, team_id, spend)
VALUES ('admin001', 'mega-team-001', 0)
ON CONFLICT (user_id, team_id) DO NOTHING;

-- 5. Create 90,000 keys (3 per user) with varied spend
INSERT INTO "LiteLLM_VerificationToken" (
    token, key_alias, user_id, team_id,
    spend, max_budget, models,
    aliases, config, permissions, metadata,
    model_spend, model_max_budget
)
SELECT
    'sk-mega-' || lpad(n::text, 5, '0') || '-' || k,
    'user' || lpad(n::text, 5, '0') || '-key-' || k,
    'user' || lpad(n::text, 5, '0'),
    'mega-team-001',
    round((random() * 80)::numeric, 2),
    100.0,
    ARRAY['my-special-fake-model-alias-name'],
    '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
    '{}'::jsonb, '{}'::jsonb
FROM generate_series(1, 30000) AS n,
     generate_series(1, 3) AS k
ON CONFLICT (token) DO NOTHING;

-- 6. Create 3 keys for admin001 in mega-team
INSERT INTO "LiteLLM_VerificationToken" (
    token, key_alias, user_id, team_id,
    spend, max_budget, models,
    aliases, config, permissions, metadata,
    model_spend, model_max_budget
)
VALUES
    ('sk-mega-admin-key-1', 'admin-mega-key-1', 'admin001', 'mega-team-001',
     15.50, 200.0, ARRAY['my-special-fake-model-alias-name'],
     '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb),
    ('sk-mega-admin-key-2', 'admin-mega-key-2', 'admin001', 'mega-team-001',
     42.75, 200.0, ARRAY['my-special-fake-model-alias-name'],
     '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb),
    ('sk-mega-admin-key-3', 'admin-mega-key-3', 'admin001', 'mega-team-001',
     3.20, 200.0, ARRAY['my-special-fake-model-alias-name'],
     '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb)
ON CONFLICT (token) DO NOTHING;

COMMIT;

-- Verify counts
SELECT 'Team members' AS label, array_length(members, 1) AS count
FROM "LiteLLM_TeamTable" WHERE team_id = 'mega-team-001'
UNION ALL
SELECT 'Users created', count(*)::int FROM "LiteLLM_UserTable" WHERE 'mega-team-001' = ANY(teams)
UNION ALL
SELECT 'Team memberships', count(*)::int FROM "LiteLLM_TeamMembership" WHERE team_id = 'mega-team-001'
UNION ALL
SELECT 'Keys created', count(*)::int FROM "LiteLLM_VerificationToken" WHERE team_id = 'mega-team-001';
