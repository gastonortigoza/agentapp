-- Codex disposable fixture schema derived from frozen contract c94f752e...
-- Run only in dedicated age50_auth_test. Roles/passwords are provisioned by driver.
CREATE SCHEMA agentapp AUTHORIZATION age50_auth_migrator;
SET ROLE age50_auth_migrator;
CREATE TABLE agentapp.users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 email text NOT NULL UNIQUE,
 password_hash text NOT NULL,
 birth_date date NOT NULL,
 role text NOT NULL DEFAULT 'member' CHECK (role='member'),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK (email=lower(btrim(email)) AND char_length(email) BETWEEN 3 AND 255)
);
CREATE TABLE agentapp.sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES agentapp.users(id) ON DELETE CASCADE,
 family_id uuid NOT NULL,
 refresh_hash text NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_family ON agentapp.sessions(family_id);
RESET ROLE;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA agentapp TO age50_auth_app,age50_auth_fixture;
GRANT SELECT,INSERT ON agentapp.users TO age50_auth_app;
-- A users-row FOR UPDATE lock requires UPDATE privilege; restrict it to id.
GRANT UPDATE(id) ON agentapp.users TO age50_auth_app;
GRANT SELECT,INSERT,UPDATE ON agentapp.sessions TO age50_auth_app;
GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON agentapp.users,agentapp.sessions TO age50_auth_fixture;
ALTER ROLE age50_auth_app SET statement_timeout='5s';
ALTER ROLE age50_auth_app SET idle_in_transaction_session_timeout='5s';
