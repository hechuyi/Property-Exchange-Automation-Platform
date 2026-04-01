# Agent Wire Bridge Design

**Date:** 2026-03-31

**Goal**

Extract the existing Claude CLI -> LiteLLM -> OpenAI Responses compatibility work into a standalone public repository so the bridge logic is maintainable, auditable, and easy to reapply without editing hidden `site-packages` state by hand.

**Problem**

The current solution works, but the important logic is spread across:

- a local launch script
- a LiteLLM config file
- patched LiteLLM runtime files under a private virtualenv

That shape is hard to review, hard to version, and hard to migrate when LiteLLM or the upstream clients evolve.

**Recommended Approach**

Create a dedicated public repository named `agent-wire-bridge` that treats LiteLLM as an upstream runtime dependency, while storing our bridge-specific behavior as first-class source artifacts:

- a documented patch bundle against a pinned LiteLLM version
- install/bootstrap scripts that create a clean environment and apply the patch
- a launch script that derives upstream OpenAI-compatible credentials from the local Codex config
- sample config files for Claude-facing model aliases
- verification scripts and fixtures that catch schema regressions early

This does not attempt a full rewrite of LiteLLM. It turns the current hidden runtime mutation into an explicit, reproducible, reviewable layer.

**Why This Approach**

This is the best tradeoff between clarity and effort.

- It makes the custom behavior visible and versioned.
- It avoids vendoring large amounts of third-party code.
- It keeps the running system close to what already works.
- It gives us a clean future migration path if we later decide to replace LiteLLM with a fully independent bridge implementation.

**Repository Boundaries**

The public repository should include only material we own or can safely redistribute:

- our patch file
- our scripts
- our docs
- redacted fixtures and tests
- example configuration

It should not include:

- API keys or private auth files
- raw local session logs
- private Claude/Codex prompts captured from production sessions
- a copied third-party `site-packages` tree

**Target Repository Structure**

```text
agent-wire-bridge/
  README.md
  LICENSE
  pyproject.toml
  .gitignore
  config/
    litellm-config.yaml
  docs/
    architecture.md
  fixtures/
    anthropic-metadata-request.json
  patches/
    litellm-1.82.6-anthropic-responses.patch
  scripts/
    bootstrap.sh
    start.sh
    verify_transform.py
```

**Core Behaviors To Preserve**

The extracted repository must preserve the working bridge behaviors already validated locally:

- Claude model aliases mapped onto OpenAI-compatible upstream models
- reasoning effort mapping
- lifting embedded `<system-reminder>` blocks into `instructions`
- omission of assistant `thinking` from replayed input
- stable tool call identifier normalization
- Anthropic usage normalization, including cached token mapping
- metadata-to-cache-key behavior compatible with Codex-style requests
- avoidance of `user` and `safety_identifier` in Responses requests when the upstream middlebox rejects them

**Verification Strategy**

Verification should happen at two levels:

1. Offline transform verification
   - run the adapter against a fixture request
   - assert `prompt_cache_key` is preserved
   - assert `user` and `safety_identifier` are absent

2. Optional live bridge verification
   - start a temporary LiteLLM instance with the patch applied
   - replay a known request shape
   - confirm the local Anthropic-compatible endpoint returns HTTP 200

**Publishing Strategy**

Publish the repository under the authenticated GitHub account `hechuyi` as a public repository. The initial release should prioritize:

- reproducibility
- documentation
- explicit pinning
- a narrow, tested surface

It does not need to solve every future client compatibility problem on day one.
