# Spark Status Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Spark GPU temperature chip with a status-dot badge (연결됨 / 이상있음 / 심각한 오류발생 / 접속끊김) and deploy to git + reviewdash.vercel.app.

**Architecture:** Client-side only. `updateSparkTemp()` maps health + temp thresholds to CSS classes; `reporter.py` embeds matching CSS into the generated dashboard HTML.

**Tech Stack:** Vanilla JS, CSS in `reporter.py` HTML template, Vercel static deploy from `output/`.

## Global Constraints

- Light dashboard theme (no dark navy pill clone)
- Thresholds: green `<75`, yellow `75–89` or missing temp, red `≥90`, gray = health fail
- Do not commit `.env` or secrets

---

### Task 1: CSS + placeholder in reporter.py

**Files:** `src/reporter.py`

- [ ] Replace `.spark-temp.cool/warm/hot/warn` with `.ok/.warn/.error/.offline`
- [ ] Update placeholder span text to `● 연결됨 --°C`

### Task 2: updateSparkTemp logic

**Files:** `src/dashboard_model_controls.js`

- [ ] Implement status labels and thresholds per spec
- [ ] Keep badge hidden when provider !== `spark`

### Task 3: Regenerate dashboard + verify

- [ ] Regenerate `output/dashboard.html` (or patch if regen unavailable)
- [ ] Confirm badge classes/strings appear in HTML/JS bundle

### Task 4: Commit, push, Vercel

- [ ] Commit design + source (exclude secrets / noisy unrelated files if inappropriate)
- [ ] Push to `origin`
- [ ] Create/link Vercel project `reviewdash`, deploy `output/` to production with domain `reviewdash.vercel.app`
