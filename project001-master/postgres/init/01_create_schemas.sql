-- =============================================================
-- 01 - Create the three schemas inside one database
-- =============================================================
--   erp       -> owned by mock-erp (ERP master data, read over HTTP)
--   audit     -> written by agent, read by streamlit-ui
--   langgraph -> LangGraph checkpointer tables (created by the agent)
--
-- This file runs automatically the FIRST time the postgres
-- container starts with an empty volume.
-- =============================================================

CREATE SCHEMA IF NOT EXISTS erp;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS langgraph;
