# Multi-provider API Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenAI as a first-class scoring provider, unify dashboard API-key entry into `.env` for Spark/OpenAI/Anthropic, and show human-readable model names (Spark health detail, strip date suffixes) in the model bar and compare UI.

**Architecture:** Keep Anthropic on Messages API. Route `spark` and `openai` through one OpenAI-compatible chat-completions helper that selects base URL and API key by provider. Generalize `/api/spark-key` into `/api/provider-key`. Centralize display-name formatting in a small helper used by snapshots and compare UI.

**Tech Stack:** Python 3, `requests`, existing `envfile` + dashboard `serve`, vanilla JS in `dashboard_model_controls.js` / `dashboard_compare.js`, pytest.

## Global Constraints

- Providers only: `spark`, `openai`, `anthropic`, `fallback`
- Keys only in `.env` + process env — never `config.json` or HTML
- OpenAI default base: `https://api.openai.com/v1`
- Spark base remains `config.ai.base_url`
- Do not change compare semantics (snapshot vs snapshot only)

---

### Task 1: `format_model_display` + Spark resolve helper

**Files:**
- Create: `src/model_display.py`
- Test: `tests/test_model_display.py`

**Interfaces:**
- Produces: `format_model_display(name: str) -> str`
- Produces: `resolve_snapshot_model(provider: str, model_id: str, spark_health: dict | None = None) -> str`

- [ ] **Step 1: Write failing tests**

```python
from src.model_display import format_model_display, resolve_snapshot_model

def test_strips_date_suffix():
    assert format_model_display("claude-haiku-4-5-20251001") == "claude haiku 4.5"

def test_strips_year_token():
    assert "2026" not in format_model_display("foo-bar-2026")

def test_spark_prefers_health_model():
    assert resolve_snapshot_model("spark", "qwen", {"ok": True, "model": "Qwen3.5-122B"}) == format_model_display("Qwen3.5-122B")
```

- [ ] **Step 2: Implement `src/model_display.py`**
- [ ] **Step 3: Run tests — expect PASS**
- [ ] **Step 4: Commit**

---

### Task 2: AIClient OpenAI provider

**Files:**
- Modify: `src/ai_client.py`
- Modify: `config.json` (optional `openai_api_key_env` default only if needed)
- Modify: `.env.example`
- Test: `tests/test_pipeline_smoke.py` or `tests/test_ai_client_providers.py`

**Interfaces:**
- Consumes: env `OPENAI_API_KEY` (or `ai.openai_api_key_env`)
- Produces: `provider in {spark, openai, anthropic, fallback}`; `list_remote_models()` for spark+openai

- [ ] **Step 1: Tests for key selection / availability per provider**
- [ ] **Step 2: Refactor `_call_openai` to use provider-specific key + base_url; add openai branch in `__init__`**
- [ ] **Step 3: `list_remote_models` for openai with Authorization header
- [ ] **Step 4: Commit**

---

### Task 3: Dashboard server — provider options, status, `/api/provider-key`

**Files:**
- Modify: `src/dashboard_server.py`
- Test: `tests/test_provider_key.py` (temp `.env` + config)

**Interfaces:**
- Produces: `save_provider_api_key(config_path, provider, key, logger) -> dict`
- Produces: status fields `openai_key_set`, providers include openai
- Produces: `POST /api/provider-key`; `/api/spark-key` delegates

- [ ] **Step 1: Failing test for save_provider_api_key writing OPENAI_API_KEY**
- [ ] **Step 2: Implement apply_provider_config openai branch + status models list**
- [ ] **Step 3: Wire routes**
- [ ] **Step 4: Commit**

---

### Task 4: Snapshot labels use display names

**Files:**
- Modify: `src/model_runs.py`
- Test: extend model_runs / model_display tests

- [ ] **Step 1: On spark snapshot, resolve via health model then `format_model_display`**
- [ ] **Step 2: Include `openai` in engine name map**
- [ ] **Step 3: Commit**

---

### Task 5: Dashboard JS + HTML key bar + compare display

**Files:**
- Modify: `src/dashboard_model_controls.js`
- Modify: `src/dashboard_compare.js` (client-side date strip mirror OR rely on server-stored names)
- Modify: `src/reporter.py` (key bar markup ids)

- [ ] **Step 1: Generic key bar; post `/api/provider-key`**
- [ ] **Step 2: OpenAI in provider select; status `openai_key_set`**
- [ ] **Step 3: Compare `formatModelDisplay` for legacy short names / date suffixes**
- [ ] **Step 4: Commit**

---

### Task 6: Verify, push, Vercel redeploy

- [ ] Run full relevant pytest
- [ ] Commit remaining + push `origin`
- [ ] `vercel --prod` from `output/` (or project root per existing setup)
