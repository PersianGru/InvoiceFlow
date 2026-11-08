# AI system inventory (EU AI Act documentation)

Updated 2026-11-04. InvoiceFlow's AI use is **document data extraction and
classification** with mandatory human oversight; no decisions about natural persons are automated.

| System | Where | Purpose | Data in | Data out | Human oversight | Logging |
|---|---|---|---|---|---|---|
| Rules extractor (`rules`) | `app/pipeline/extract/rules.py` | deterministic field extraction (regex, keywords) | document text | fields with confidence and location | review queue below the firm's confidence threshold | timeline event `extract` |
| Local LLM (`llm_local`, Ollama, default `qwen2.5:7b`) | `app/pipeline/extract/llm.py` | field extraction with JSON-schema output | document text | fields with confidence | same; AI-generated values marked until confirmed | `ai_call_log` (external=false), timeline |
| Cloud LLM (`llm_cloud`, Claude Messages API) | `app/pipeline/extract/llm.py` | fallback/first extraction **only** when the license carries `cloud_ai` and the firm opted in | document text | fields with confidence | same | `ai_call_log` (external=true), timeline, per-firm opt-in audited |
| OCR (Tesseract) | `app/pipeline/text.py` | text recognition on images / scanned pages | page images | words with boxes and confidence | implicit (feeds the extractors) | timeline event `text` |
| Classifier | `app/pipeline/classify.py` | document type sanity, purchase/sales, GL/VAT suggestions | extracted fields, supplier profile | suggestions | every suggestion is editable | — |

## Transparency and marking (Art. 50)

- Every extracted value carries `meta.<field>.ai_generated = true` with `source`, `model` and
  `prompt_version` until a human confirms it (approval flips the flag and records who/when).
- Chat channels disclose the automated system in the first message of every conversation.

## Prompt-injection safety

Document text is untrusted: the LLM may only return the schema (`format`/tool schema), the text
is fenced in the prompt as data, the output is validated against the canonical model, and no model
output triggers actions, tool calls or writes to other records.

## Model and prompt versioning

`prompt_version = extract-v1`; the model name is stored per extraction (`extractions.model`) and per
value (`meta.<field>.model`). Changing either requires a run of the evaluation harness
(`backend/evalharness`) and a note in `CHANGELOG.md`.

## Providers and data retention

- Local: Ollama inside the deployment; nothing leaves the network.
- Cloud: the Claude API, used only with the entitlement and opt-in; prefer zero-data-retention
  terms (`TODO(legal)`: confirm the contractual terms before enabling for a firm).
