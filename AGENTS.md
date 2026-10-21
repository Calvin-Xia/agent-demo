# Project Instructions

## Purpose

This repository contains a minimal, transparent image-inspection Agent demo for exploring observable tool-call workflows. It has two paths: a scripted offline demo (no API key) and a minimal LLM harness that connects an OpenAI-compatible model to the same inspection tools.

## Run and verify

```bash
uv sync --locked
uv run python -m rs_agent inspect examples/sample.ppm
uv run pytest -q
```

The LLM harness is optional and needs a key: copy `.env.example` to `.env` and fill `DEEPSEEK_API_KEY`, then run `uv run python -m rs_agent chat "检查 examples/sample.ppm"` (or `--api responses`); see README for configuration. Interactive sessions start with a profile/protocol/model/thinking menu (`--no-menu` skips it); `--profile <name>` reuses a saved profile.

Before handoff, also run the missing-path CLI case and `git diff --check`.

## Stack

- Python 3.12 or newer, managed with `uv` and committed `uv.lock`.
- Pillow is the only runtime dependency; pytest is the only test dependency.
- The CLI uses standard-library `argparse` and `json`; HTTP and SSE handling in the harness also use only the standard library.

## Structure and conventions

- Keep importable code under `src/rs_agent/` and tests under `tests/`.
- Keep the scripted `inspect` policy explicitly scripted; do not describe it as an LLM. Only the `chat` harness talks to a model.
- `inspect_image` must return JSON-serializable data and translate expected image failures into `ImageInspectionError`.
- Preserve the five public trace stages: task, decision, action, observation, final.
- Prefer direct functions and small modules; do not add framework abstractions for a single tool.
- README is the usage and roadmap authority. Detailed mechanics live in `docs/`.
- Named profiles live in `.rs_agent_profiles.json` (gitignored); they reference keys by env var name (`api_key_env`) and are managed with `--profile` or the interactive `/save` / `/use` commands.

## Current status and next step

The minimal offline inspection MVP, deterministic tests, and the minimal LLM harness are implemented: chat/completions + responses formats, `.env` credential loading, interactive profile/protocol/model/thinking menu, named profiles persisted in `.rs_agent_profiles.json`, and the `/help` `/profiles` `/config` `/use` `/save` `/delete` slash commands. Treat unchecked README roadmap items as separate future changes; do not expand this MVP into GeoTIFF, NDVI, or multi-agent work incidentally.
