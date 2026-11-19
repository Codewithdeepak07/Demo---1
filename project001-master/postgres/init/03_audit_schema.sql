-- =============================================================
-- 03 - Audit schema (our design, based on the suggested schema)
-- =============================================================
-- Rules we follow:
--   * Line items are their own rows (not JSON inside the invoice).
--   * Human corrections are INSERTs into human_feedback, never
--     UPDATEs of the original extracted values.
--   * Money uses NUMERIC, dates use DATE / TIMESTAMPTZ.
--   * The full raw extracted text is kept.
--
-- Additions to the suggested schema:
--   * invoice_audit: vendor_id, subtotal, tax_amount, processing_status,
--     error_message (so failed files are recorded too).
--   * discrepancies: validation_run, line_number, check_type, rule, message
--     (so the first run and every re-validation are kept side by side).
--   * validation_reports: one row per validation run holding the full
--     JSON report (original discrepancies + recalculated data).
-- =============================================================

CREATE TABLE IF NOT EXISTS audit.invoice_audit (
    invoice_id              UUID PRIMARY KEY,
    file_path               VARCHAR(512),            -- reference, not the file
    file_checksum           VARCHAR(64) UNIQUE,      -- detects reprocessing
    extraction_method       VARCHAR(32),             -- pdfplumber | docx | tesseract
    raw_extracted_text      TEXT,                    -- full text, pre-translation
    translated_text         TEXT,
    source_language         VARCHAR(8),
    translation_confidence  NUMERIC(4,3),
    invoice_number          VARCHAR(64),
    vendor_id               VARCHAR(32),
    vendor_name             VARCHAR(255),
    po_number               VARCHAR(64),
    invoice_date            DATE,
    currency                CHAR(3),
    subtotal                NUMERIC(14,2),
    tax_amount              NUMERIC(14,2),
    total_amount            NUMERIC(14,2),
    validation_status       VARCHAR(32),             -- passed | failed | partial
    recommendation          VARCHAR(32),             -- approve | manual_review | reject
    processing_status       VARCHAR(32) DEFAULT 'detected', -- detected | processed | failed
    error_message           TEXT,
    extracted_raw           JSONB,                   -- variable leftovers + email meta
    thread_id               VARCHAR(128),            -- links to langgraph state
    processed_at            TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.invoice_line_items (
    id           BIGSERIAL PRIMARY KEY,
    invoice_id   UUID REFERENCES audit.invoice_audit(invoice_id),
    line_number  INTEGER,
    item_code    VARCHAR(64),
    description  TEXT,
    quantity     NUMERIC(12,3),
    unit_price   NUMERIC(14,4),
    line_total   NUMERIC(14,2)
);

CREATE TABLE IF NOT EXISTS audit.discrepancies (
    id              BIGSERIAL PRIMARY KEY,
    invoice_id      UUID REFERENCES audit.invoice_audit(invoice_id),
    validation_run  INTEGER DEFAULT 1,               -- 1 = first run, 2+ = re-validation
    line_number     INTEGER,                         -- NULL for header level issues
    check_type      VARCHAR(16),                     -- data | business
    rule            VARCHAR(32),                     -- missing_field | total_mismatch | price_mismatch ...
    field_name      VARCHAR(64),
    invoice_value   TEXT,
    erp_value       TEXT,
    deviation_pct   NUMERIC(12,4),
    severity        VARCHAR(16),                     -- high | medium
    message         TEXT,
    detected_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.human_feedback (
    id               BIGSERIAL PRIMARY KEY,
    invoice_id       UUID REFERENCES audit.invoice_audit(invoice_id),
    field_name       VARCHAR(64),
    original_value   TEXT,
    corrected_value  TEXT,
    corrected_by     VARCHAR(128),
    corrected_at     TIMESTAMPTZ DEFAULT now(),
    revalidated      BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS audit.validation_reports (
    id                 BIGSERIAL PRIMARY KEY,
    invoice_id         UUID REFERENCES audit.invoice_audit(invoice_id),
    validation_run     INTEGER,
    run_type           VARCHAR(16),                  -- initial | revalidation
    validation_status  VARCHAR(32),
    recommendation     VARCHAR(32),
    report_json        JSONB,
    json_path          VARCHAR(512),
    html_path          VARCHAR(512),
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- Indexes for the filters the dashboard and SQL answers use most
CREATE INDEX IF NOT EXISTS idx_invoice_vendor        ON audit.invoice_audit (vendor_name);
CREATE INDEX IF NOT EXISTS idx_invoice_date          ON audit.invoice_audit (invoice_date);
CREATE INDEX IF NOT EXISTS idx_invoice_recommendation ON audit.invoice_audit (recommendation);
CREATE INDEX IF NOT EXISTS idx_lines_invoice         ON audit.invoice_line_items (invoice_id);
CREATE INDEX IF NOT EXISTS idx_disc_invoice_run      ON audit.discrepancies (invoice_id, validation_run);
CREATE INDEX IF NOT EXISTS idx_feedback_invoice      ON audit.human_feedback (invoice_id);
CREATE INDEX IF NOT EXISTS idx_reports_invoice_run   ON audit.validation_reports (invoice_id, validation_run);
