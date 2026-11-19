# AI Invoice Auditor

A containerized, multilingual invoice validation system. Invoices dropped into a folder are
extracted (PDF / DOCX / scanned PNG), translated to English, validated against an ERP service,
reported on, and indexed for natural-language questions. A Streamlit dashboard lets an auditor
correct values and re-validate.

## 1. Folder structure (one folder per container)

```
invoice-auditor/
├── compose.yaml              # the 6 containers
├── .env.example              # copy to .env
├── postgres/init/            # container 1 - schemas, ERP seed data, audit schema (auto-loaded)
├── qdrant/config/            # container 2 - Qdrant config
├── redis/redis.conf          # container 3 - cache config (no persistence on purpose)
├── mock-erp/                 # container 4 - FastAPI over the erp schema
├── agent/                    # container 5 - LangGraph agents + FastAPI
│   ├── config/               #   rules.yaml, prompts.yaml (prompt templates + personas)
│   └── app/
│       ├── agents/           #   monitor, extractor, translator, field_parser, data_validator,
│       │                     #   business_validator, recalculator, reporter
│       ├── db/               #   audit-schema SQL (the only place SQL is written)
│       ├── services/         #   LiteLLM client, Redis cache, mock-erp HTTP client, Qdrant
│       ├── rag/              #   indexer, retriever, augmenter, generator, reflector, sql_answerer
│       ├── workflows/        #   invoice_graph, revalidation_graph, rag_graph, shared nodes
│       └── templates/        #   HTML report template
├── streamlit-ui/             # container 6 - dashboard
└── shared/                   # bind-mounted folders (not a container)
    ├── data/incoming/        #   drop invoices here (+ optional .meta.json email header)
    └── outputs/reports/      #   generated JSON + HTML reports
```

## 2. Configure and start

```bash
cp .env.example .env          # then fill in AWS keys and a DB password
docker compose up --build -d
docker compose ps             # all six should become "healthy"
```

| What | URL (from your browser) |
|---|---|
| Dashboard | http://localhost:8501 |
| Agent API docs | http://localhost:8000/docs |
| Mock ERP API docs | http://localhost:8001/docs |
| Qdrant dashboard | http://localhost:6333/dashboard |

The AWS account needs Bedrock model access to **Amazon Nova Lite**, **Cohere Command R+** and
**Titan Text Embeddings v2** in `AWS_REGION_NAME`.

> Linux note: the agent runs as UID 1000 and writes reports to `shared/outputs/reports`.
> If your host user is not UID 1000, run `chmod 777 shared/outputs/reports` once.

## 3. How an invoice flows

```
incoming file ─► Monitor (checksum, skip if already audited, route by type)
   ─► Extractor (pdfplumber → OCR fallback | python-docx | Tesseract eng+spa+deu)  → raw text saved
   ─► Translator (Command R+, confidence score, cached in Redis)                   → translation saved
   ─► Field parser (Nova Lite → header + line items)                               → typed columns + line rows
   ─► ERP lookup (HTTP: /po, /vendor, /sku on mock-erp:8000)
   ─► Data validation (required fields, types, currency, arithmetic, tax)
   ─► Business validation (qty / price / currency / tax vs PO, tolerances from rules.yaml)
                                                                                    → discrepancies saved
   ─► Reporter (approve / manual_review / reject, JSON + HTML)                      → outcome + report saved
   ─► Indexer (chunks of invoice text + report text → Qdrant)
```

Recommendation policy (from `rules.yaml`):
* invalid currency → **reject**
* missing field (`flag`) or total mismatch, or any ERP mismatch → **manual review**
* no discrepancies and translation confidence ≥ 0.95 → **approve**

## 4. Correct and re-validate (human in the loop)

1. In **Review & Correct**, edit a header field or a line item and click **Save corrections**.
   Each change is an `INSERT` into `audit.human_feedback`; the original extraction is never overwritten.
2. Click **Re-validate**. The agent:
   * starts again from the **original** extraction and applies the latest correction for each field,
   * fetches fresh PO / vendor / SKU data from mock-erp,
   * **recalculates every line from the start**: line total = qty × unit price, line tax = line total × SKU GST rate,
     then subtotal, tax and total; and next to each line it computes what the ERP PO says,
   * re-runs data and business validation,
   * writes a new report holding **the previous discrepancies, the corrections, the line-by-line
     recalculation vs ERP, and the new result**.
3. Everything is stored as a new run: `audit.discrepancies` (with `validation_run`),
   `audit.validation_reports` (full JSON report), `human_feedback.revalidated = true`, and the latest
   decision on `audit.invoice_audit`. JSON + HTML files go to `shared/outputs/reports/<invoice>_runN.*`.

Example with the sample data: INV-1001 bills gloves at 3.50 against 3.00 on PO-1001 (+16.7%) → manual review.
Correct line 2 unit price to 3.00 → re-validate → totals recalculate to 1,410.00 + 141.00 = 1,551.00, which
matches the ERP → approve.

## 5. Asking questions

**Ask the Auditor** routes each question:
* counting / listing / totals → a generated, read-only `SELECT` on the `audit` schema;
* open questions → Qdrant retrieval → rerank → Command R+ answer → Nova Lite RAG-Triad reflection.
  If the average triad score is ≤ 0.7, retrieval is retried wider (max 3 retries).
* A repeated question is answered from Redis.

## 6. Data placement

| Store | Holds | Survives `down`? | Survives `down -v`? |
|---|---|---|---|
| `shared/` bind mounts | invoice files, report files | yes | yes (host folder) |
| PostgreSQL (`postgres_data`) | ERP data, audit trail, LangGraph checkpoints | yes | no |
| Qdrant (`qdrant_data`) | vectors + payloads | yes | no |
| Redis (no volume) | LLM + answer cache | no, and nothing breaks | no |

The invoice binary never enters PostgreSQL; only its path and checksum do. Qdrant is derived from
PostgreSQL: if its volume is lost the agent rebuilds the index on startup (or click **♻️ Rebuild index** in the Ask the Auditor sidebar, which calls `POST /reindex`);
no OCR or translation is repeated.

## 7. Verification checklist

```bash
# Check 1 - single command startup
docker compose down -v && docker compose up --build -d && docker compose ps

# Check 2 - end to end (the sample invoices are already in shared/data/incoming)
docker compose logs -f agent
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT invoice_number, vendor_name, recommendation FROM audit.invoice_audit;"

# Check 3 - persistence across restart (volumes kept)
docker compose down && docker compose up -d
# audit trail still there, Ask the Auditor still answers, nothing is reprocessed (checksums)

# Check 4 - deliberate data loss
docker compose down -v && docker compose up -d
# audit trail and index are empty: -v deleted the named volumes.
# (Files in shared/data/incoming are then processed again because their checksums are gone.)

# Check 5 - no secrets in images
docker history invoice-auditor/agent:1.0 --no-trunc
docker history invoice-auditor/mock-erp:1.0 --no-trunc
docker history invoice-auditor/streamlit-ui:1.0 --no-trunc

# Check 6 - service-name resolution
docker compose exec agent getent hosts postgres qdrant mock-erp redis
```

## 8. Design notes (talking points)

* **Service boundary:** only mock-erp reads the `erp` schema. The agent uses HTTP; the UI only reads
  `audit` (its DB session is forced read-only) and calls the agent for every change.
* **Scaling:** mock-erp and streamlit-ui are stateless and can be scaled horizontally. The agent's folder
  monitor would process files twice if run as several replicas (it would need a queue or a lock first).
  PostgreSQL is a single writer instance.
* **Restart policy vs start order:** `restart: unless-stopped` only re-runs a crashed container; it does not
  make it wait. `depends_on: condition: service_healthy` + health checks fix the order, and the agent also
  refuses to start if a dependency is unreachable.
* **Incoming folder as a bind mount** is a dev convenience. In production files would land in object storage
  (S3) so several agent instances could share them, with durability and access control.
* **LiteLLM in-process:** simpler for this build. As a separate gateway it would centralise rate limiting,
  cost tracking and credentials.
* **Logging:** `rules.yaml` lists a `log_file`, but containers must log to stdout, so the agent only
  uses `log_level` from that section.

## 9. Notes on the sample data

* `erp_schema.sql` was not supplied, so `postgres/init/02_erp_schema.sql` was written from the
  ERP mock JSON files (`shared/data/ERP_mockdata`).
* `INV_DE_004.meta.json` lists a `.docx`, but the file supplied is a `.pdf`; both formats are supported.
* INV-1002 and INV-1005 print no item codes: `item_code` is flagged as a missing required field, and
  lines are matched to the PO by description so the ERP comparison still runs.
* The scanned INV-1005 shows no currency symbol, so currency is flagged as missing. An auditor can set it
  to USD and re-validate.
* `Actual_invoice.pdf` has no PO in the ERP, so it ends in manual review.
# capstone1
