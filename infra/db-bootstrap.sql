-- Run once as the master user against database cms (steps in README.md, "The database").
-- Same statements as initdb/01-init.sh; RDS has no initdb. Not repeatable: a second run fails
-- on CREATE ROLE, which is the intent. The password comes from the environment, never this file.
\set ON_ERROR_STOP on
\getenv app_pw APP_PW
CREATE EXTENSION IF NOT EXISTS vector;
CREATE ROLE cms_app LOGIN PASSWORD :'app_pw';
GRANT CONNECT ON DATABASE cms TO cms_app;
-- PostgreSQL 15+ no longer grants CREATE on schema public to PUBLIC.
GRANT USAGE, CREATE ON SCHEMA public TO cms_app;
