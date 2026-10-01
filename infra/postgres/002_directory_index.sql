-- Additive evolution of the AGE-31 directory query; no DTO or data changes.
CREATE INDEX profiles_directory_geo ON agentapp.profiles
  (country_id, province_id, zone_id, created_at DESC, id DESC) WHERE published;
