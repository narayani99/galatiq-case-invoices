# Galatiq Case: Automated Invoice Processing System

A multi-agent, local-first system to automate Acme Corp's invoice processing workflow, resolving their **$2M/year** manual processing leak.

Built using **Python**, **FastAPI**, **LangGraph**, **React + TypeScript**, and **SQLite**.

---

## Architecture

The system executes a linear 5-stage processing pipeline for each invoice:

```
[Invoice Document]
       │
       ▼
1. Ingestion Agent (Multi-Format Parser + LLM Fallback)
       │
       ▼
2. Validation Agent (SQLite Inventory Checks)
       │
       ▼
3. Approval Agent (Business Rules + Escalated LLM Reflection)
       │
       ▼
4. Payment Agent (Mock API Gateway with 5% Failure Rate)
       │
       ▼
5. Audit Logger (Persisted lineage, duration, & reasoning)
```

### Key Design Elements:
- **Resilience & State Persistence:** LangGraph saves the `ProcessingState` to SQLite after every node execution. If a stage fails (e.g., payment gateway timeout), it can be **resumed** from that exact node without re-running previous stages.
- **Parser Fallback:** If the LLM is offline or has credit issues, the ingestion pipeline falls back to high-fidelity regex/parser output automatically (with confidence scores set to `0.5`).
- **Idempotent Payments:** Prevent duplicate payments on retries by matching the unique `invoice_id` against previous successful payments.

---

## Tech Stack & Dependencies

- **Orchestrator:** LangGraph
- **Agents & LLM:** xAI Grok (OpenAI-compatible SDK)
- **REST API:** FastAPI (Uvicorn server)
- **Database:** SQLite (local-only, standard sqlite3 driver)
- **Frontend Dashboard:** React 19 + TypeScript, Recharts, Lucide icons, Vanilla custom CSS
- **Test Suite:** pytest, pytest-asyncio

---

## Getting Started

### 1. Installation
In the project root, create a virtual environment, activate it, and install the dependencies:

```bash
python -m venv .venv
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure API Keys
The system is configured to read the xAI Grok API key from a `.api-key` file in the project root. If you have a key, add it there:
```text
xai-your-api-key-here
```
*Note: If no API key is present or it is invalid, the system automatically runs in high-fidelity parser/rule fallback mode.*

### 3. Initialize the Database
Set up the SQLite database containing the mock inventory and vendor registry:
```bash
python main.py init-db
```
This seeds the database with the required WidgetA, WidgetB, GadgetX, and FakeItem stocks.

---

## Running the System

### Run Dev Servers (API + Dashboard)
Run the script to launch both servers simultaneously:

**On Windows:**
```powershell
./run_local.ps1
```

**On macOS/Linux:**
```bash
./run_local.sh
```

- **Frontend UI:** http://localhost:5173/
- **FastAPI backend docs:** http://localhost:8000/docs

---

## CLI Reference

You can also run individual operations or batches from the command-line tool:

### 1. Process a Single Invoice
Processes an invoice through all 5 stages and outputs the final JSON result to stdout:
```bash
python main.py process --invoice_path=data/invoices/invoice_1001.txt
```

### 2. Process a Batch
Sequentially processes all supported invoices inside a directory, printing a progress indicator and final status table:
```bash
python main.py process-batch --invoice_dir=data/invoices
```

### 3. Resume a Failed Invoice
Resumes processing of a failed or errored invoice from the last successfully completed stage:
```bash
python main.py resume --invoice_id=INV-1001
```

---

## Running Tests

Verify agent logic, parsers, and inventory check outcomes:
```bash
python -m pytest tests/
```
All 10 integration and unit tests are green.
