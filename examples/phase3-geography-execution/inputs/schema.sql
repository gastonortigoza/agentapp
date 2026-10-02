-- Codex disposable geography fixture derived from the frozen A25 contract.
-- Only age50_geography_test; no official catalogue or persistent service roles.
CREATE SCHEMA agentapp AUTHORIZATION age50_geo_migrator;
SET ROLE age50_geo_migrator;
CREATE TABLE agentapp.countries (
 id uuid PRIMARY KEY, code text NOT NULL CHECK (code='AR'), name text NOT NULL,
 UNIQUE(code)
);
CREATE TABLE agentapp.provinces (
 id uuid PRIMARY KEY, country_id uuid NOT NULL REFERENCES agentapp.countries(id),
 name text NOT NULL, UNIQUE(id,country_id), UNIQUE(country_id,name)
);
CREATE TABLE agentapp.zones (
 id uuid PRIMARY KEY, country_id uuid NOT NULL, province_id uuid NOT NULL,
 name text NOT NULL,
 FOREIGN KEY(province_id,country_id) REFERENCES agentapp.provinces(id,country_id),
 UNIQUE(id,province_id,country_id), UNIQUE(province_id,name)
);
RESET ROLE;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA agentapp FROM PUBLIC;
GRANT USAGE ON SCHEMA agentapp TO age50_geo_app,age50_geo_fixture;
GRANT SELECT ON ALL TABLES IN SCHEMA agentapp TO age50_geo_app;
GRANT SELECT,INSERT,DELETE ON ALL TABLES IN SCHEMA agentapp TO age50_geo_fixture;
