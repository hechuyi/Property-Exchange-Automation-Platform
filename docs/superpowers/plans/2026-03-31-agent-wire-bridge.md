# Agent Wire Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the current local Claude-to-Responses bridge into a standalone public repository with reproducible setup, documented patches, and basic verification.

**Architecture:** Create a new repository that treats LiteLLM as a pinned upstream dependency and stores our custom compatibility work as an explicit patch bundle plus scripts, fixtures, and docs. Keep the initial release narrow: reproducible bootstrap, deterministic patch application, launch wiring, and regression verification around the request-shape bug we just fixed.

**Tech Stack:** Python, shell scripts, LiteLLM 1.82.6, Git, GitHub CLI

---

### Task 1: Create Repository Skeleton

**Files:**
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/README.md`
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/LICENSE`
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/.gitignore`
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/pyproject.toml`
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/config/litellm-config.yaml`
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/docs/architecture.md`

- [ ] **Step 1: Create the repository directories and metadata files**
- [ ] **Step 2: Write the README with architecture, install flow, and scope boundaries**
- [ ] **Step 3: Add pinned project metadata and license**
- [ ] **Step 4: Review the tree and confirm it matches the planned layout**

### Task 2: Generate Clean Upstream Patch Bundle

**Files:**
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/patches/litellm-1.82.6-anthropic-responses.patch`

- [ ] **Step 1: Install a clean temporary LiteLLM 1.82.6 copy outside production**
- [ ] **Step 2: Diff the clean copy against the current patched runtime files**
- [ ] **Step 3: Reduce the diff to the three Anthropic Responses adapter files we actually customize**
- [ ] **Step 4: Save the final patch bundle under `patches/`**
- [ ] **Step 5: Verify the patch applies cleanly to a fresh LiteLLM 1.82.6 install**

### Task 3: Add Bootstrap And Launch Scripts

**Files:**
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/scripts/bootstrap.sh`
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/scripts/start.sh`

- [ ] **Step 1: Write `bootstrap.sh` to create a venv, install LiteLLM, and apply the patch**
- [ ] **Step 2: Write `start.sh` to derive upstream base URL and API key from `~/.codex` and launch the patched server**
- [ ] **Step 3: Make the scripts executable**
- [ ] **Step 4: Run a dry bootstrap in an isolated temp directory and confirm the patching flow completes**

### Task 4: Add Fixtures And Verification

**Files:**
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/fixtures/anthropic-metadata-request.json`
- Create: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/scripts/verify_transform.py`

- [ ] **Step 1: Create a redacted fixture that exercises metadata-derived cache key handling**
- [ ] **Step 2: Write `verify_transform.py` to assert `prompt_cache_key` exists while `user` and `safety_identifier` do not**
- [ ] **Step 3: Run the verification script against the patched environment**
- [ ] **Step 4: Record the exact command and expected output in the README**

### Task 5: Initialize Git And Publish

**Files:**
- Modify: `/Users/rtoc/Documents/WorkSpace/agent-wire-bridge/*`

- [ ] **Step 1: Initialize git in the new repository**
- [ ] **Step 2: Review the full diff for secrets or accidental local paths**
- [ ] **Step 3: Create the initial commit**
- [ ] **Step 4: Create the public GitHub repository under `hechuyi`**
- [ ] **Step 5: Push `main`**

### Task 6: Final Verification

**Files:**
- Verify only

- [ ] **Step 1: Run the local verification script from the new repository**
- [ ] **Step 2: Re-run one live request-shape replay against the production bridge**
- [ ] **Step 3: Confirm the public repository remote is configured correctly**
- [ ] **Step 4: Summarize what was extracted and what still remains local-only**
