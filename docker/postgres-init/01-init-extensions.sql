-- Runs once, automatically, only against a brand-new (empty) postgres data
-- volume -- the official postgres/pgvector images execute every .sql file in
-- /docker-entrypoint-initdb.d on first init and never again afterward. The
-- app's own schema (database/Oikos.sql) assumes this extension already
-- exists in whichever database it connects to.
CREATE EXTENSION IF NOT EXISTS vector;
