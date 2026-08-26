# Project Instructions

## Purpose

This repository contains a minimal, transparent, offline image-inspection Agent demo for exploring observable tool-call workflows.

## Run and verify

```bash
uv sync --locked
uv run python -m rs_agent inspect examples/sample.ppm
uv run pytest -q
```

Before handoff, also run the missing-path CLI case and `git diff --check`.

## Stack

- Python 3.12 or newer, managed with `uv` and committed `uv.lock`.
- Pillow is the only runtime dependency; pytest is the only test dependency.
- The CLI uses standard-library `argparse` and `json`.

## Structure and conventions

- Keep importable code under `src/rs_agent/` and tests under `tests/`.
- Keep the Agent policy explicitly scripted; do not describe it as an LLM.
- `inspect_image` must return JSON-serializable data and translate expected image failures into `ImageInspectionError`.
- Preserve the five public trace stages: task, decision, action, observation, final.
- Prefer direct functions and small modules; do not add framework abstractions for a single tool.
- README is the usage and roadmap authority. Detailed mechanics live in `docs/minimal-image-agent-tool-walkthrough.md`.

## Current status and next step

The minimal offline inspection MVP and deterministic tests are implemented. Treat unchecked README roadmap items as separate future changes; do not expand this MVP into LLM, GeoTIFF, NDVI, or multi-agent work incidentally.
