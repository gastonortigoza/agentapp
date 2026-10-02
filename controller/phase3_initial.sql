-- AGE-34 migration 001. Audited by Codex. Scope: local disposable PostgreSQL.
-- Base: agent-local draft, preserved separately. Schema owner provisioned by driver.

CREATE TABLE agentapp.users (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  email text NOT NULL,
  password_hash text NOT NULL,
  birth_date date NOT NULL,
  role text NOT NULL DEFAULT 'member',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (email),
  CHECK (role = 'member'),
  CHECK (email = lower(btrim(email)) AND char_length(email) BETWEEN 3 AND 255)
);

CREATE TABLE agentapp.countries (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  code text NOT NULL,
  name text NOT NULL,
  PRIMARY KEY (id),
  UNIQUE (code),
  CHECK (code = 'AR')
);

CREATE TABLE agentapp.provinces (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  country_id uuid NOT NULL,
  name text NOT NULL,
  PRIMARY KEY (id),
  UNIQUE (id, country_id),
  UNIQUE (country_id, name),
  FOREIGN KEY (country_id) REFERENCES agentapp.countries(id)
);

CREATE TABLE agentapp.zones (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  country_id uuid NOT NULL,
  province_id uuid NOT NULL,
  name text NOT NULL,
  PRIMARY KEY (id),
  UNIQUE (id, province_id, country_id),
  UNIQUE (province_id, name),
  FOREIGN KEY (province_id, country_id) REFERENCES agentapp.provinces(id, country_id)
);

CREATE TABLE agentapp.profiles (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  display_name text NOT NULL,
  gender text NOT NULL,
  country_id uuid NOT NULL,
  province_id uuid NOT NULL,
  zone_id uuid NOT NULL,
  description text NOT NULL,
  phone_e164 text NOT NULL,
  published boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (user_id),
  FOREIGN KEY (user_id) REFERENCES agentapp.users(id),
  FOREIGN KEY (zone_id, province_id, country_id) REFERENCES agentapp.zones(id, province_id, country_id),
  CHECK (char_length(description) BETWEEN 1 AND 2000)
);

CREATE TABLE agentapp.photos (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  profile_id uuid NOT NULL,
  object_key text NOT NULL,
  is_main boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (object_key),
  FOREIGN KEY (profile_id) REFERENCES agentapp.profiles(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX photos_one_main ON agentapp.photos(profile_id) WHERE is_main;

CREATE TABLE agentapp.plans (
  id text NOT NULL,
  amount_minor integer NOT NULL,
  currency text NOT NULL,
  billing_period text NOT NULL,
  enabled boolean NOT NULL,
  PRIMARY KEY (id),
  CHECK (id IN ('basic', 'promoted')),
  CHECK (amount_minor > 0),
  CHECK (currency = 'ARS'),
  CHECK (billing_period = 'monthly')
);

CREATE TABLE agentapp.subscriptions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  plan_id text NOT NULL,
  origin text NOT NULL,
  amount_minor integer NOT NULL,
  currency text NOT NULL,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  FOREIGN KEY (user_id) REFERENCES agentapp.users(id),
  FOREIGN KEY (plan_id) REFERENCES agentapp.plans(id),
  CHECK (origin IN ('simulated', 'rebill')),
  CHECK (amount_minor > 0),
  CHECK (currency = 'ARS'),
  CHECK (ends_at > starts_at)
);

CREATE TABLE agentapp.idempotency (
  user_id uuid NOT NULL,
  key text NOT NULL,
  payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
  response_status integer NOT NULL CHECK (response_status BETWEEN 100 AND 599),
  response_body jsonb NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key),
  FOREIGN KEY (user_id) REFERENCES agentapp.users(id) ON DELETE CASCADE,
  CHECK (expires_at > created_at)
);

CREATE TABLE agentapp.sessions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  family_id uuid NOT NULL,
  refresh_hash text NOT NULL CHECK (refresh_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (refresh_hash),
  FOREIGN KEY (user_id) REFERENCES agentapp.users(id) ON DELETE CASCADE,
  CHECK (expires_at > created_at)
);

CREATE TABLE agentapp.password_resets (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  token_hash text NOT NULL CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (token_hash),
  FOREIGN KEY (user_id) REFERENCES agentapp.users(id) ON DELETE CASCADE,
  CHECK (expires_at > created_at)
);
CREATE INDEX subscriptions_user_interval ON agentapp.subscriptions(user_id,starts_at,ends_at);
CREATE INDEX sessions_family ON agentapp.sessions(family_id);
CREATE INDEX idempotency_expiry ON agentapp.idempotency(expires_at);
