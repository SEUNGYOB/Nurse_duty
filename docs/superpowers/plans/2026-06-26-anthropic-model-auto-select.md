# Anthropic Model Auto-Select Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically choose a current stable Claude Sonnet model from Anthropic when `ANTHROPIC_MODEL` is not set, while keeping a manual override and a short fallback list.

**Architecture:** Keep selection logic inside `ocr/claude_parser.py`. First honor `ANTHROPIC_MODEL`, then query Anthropic's models API via the installed SDK, select the newest stable `sonnet` model by `created_at`, and finally fall back to a short static list if discovery or requests fail. Log the final choice once per process so operators can see what was used.

**Tech Stack:** Python 3.12, Anthropic Python SDK, stdlib `logging`, stdlib `unittest`

---

### Task 1: Add model discovery and selection helpers

**Files:**
- Modify: `ocr/claude_parser.py`

- [ ] **Step 1: Read the current Claude request flow**

Inspect the existing `send_claude_request_with_model_fallbacks()` and current model constants in `ocr/claude_parser.py` so the new helpers plug into the same retry path.

- [ ] **Step 2: Implement stable Sonnet selection**

Add helpers that:

1. normalize model IDs,
2. detect preview/beta-style model names,
3. inspect `client.models.list(limit=1000)`,
4. filter for names containing `sonnet`,
5. exclude preview/beta/experimental entries,
6. choose the newest remaining model by `created_at`.

- [ ] **Step 3: Build the final candidate order**

Add a helper that returns candidates in this order:

1. `ANTHROPIC_MODEL` if set,
2. the discovered latest stable Sonnet model,
3. the existing static fallback trio: `claude-sonnet-4-6`, `claude-opus-4-8`, `claude-haiku-4-5`.

Deduplicate while preserving order.

- [ ] **Step 4: Log the resolved model choice**

Emit a log line showing the selected model order and another line when a model actually succeeds.

### Task 2: Add unit tests for the pure selection logic

**Files:**
- Create: `tests/test_claude_model_selection.py`

- [ ] **Step 1: Write the failing tests**

Cover two pure behaviors:

1. newest stable Sonnet wins over older Sonnet and over preview entries,
2. candidate composition keeps override first and removes duplicates.

Use `types.SimpleNamespace` plus fixed `datetime` values so the tests do not need network access.

- [ ] **Step 2: Run the test file**

Run: `./.venv/bin/python -m unittest tests.test_claude_model_selection -v`

Expected: fail before implementation, then pass after the helpers are added.

- [ ] **Step 3: Verify the module still compiles**

Run: `python3 -m py_compile ocr/claude_parser.py ocr/duty_parser.py server.py`

Expected: no output.

### Task 3: Validate runtime behavior

**Files:**
- Modify: `ocr/claude_parser.py`

- [ ] **Step 1: Smoke test the selection path**

Use a small inline Python check to confirm the module imports, the candidate order is non-empty, and the fallback list still includes the known stable Claude IDs.

- [ ] **Step 2: Redeploy**

Run the existing Vercel production deploy command so the live site picks up the new selection behavior.

