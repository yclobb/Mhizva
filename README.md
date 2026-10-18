# Mhizva

A local, lightweight AI that answers questions from your own documents. Built for people with no tech knowledge: drop files in, ask in plain words (English or 中文), and get an answer with its source, plus totals, tables, charts and trends across many documents.

**MDTree** is its retrieval engine. Documents are stored as markdown in a folder tree where every folder has an `index.md`. A question descends the tree one small index at a time, splitting a reading budget across likely branches (flow-based branching), so a small local model with a short context can search a large collection. Numbers and dates are extracted at upload, so totals, charts and trends are computed in code, never guessed by the model.

## Layout
- `engine/`: MDTree (tree storage, filing, flow search, facts/totals/trends, model client)
- `server.py`: local server for the UI and the JSON API on http://127.0.0.1:8765
- `ui/`: the app (plain HTML/CSS/JS, works offline, English/中文)
- `tests/`: `python -I -X utf8 tests/<name>.py` for test_engine, test_budget, test_facts, test_chinese
- `demo_docs/`: sample business documents

## Run
- No model (keyword stand-in): `python -I server.py --fake`
- With a local model: put the official llama.cpp Windows CPU build in `runtime/` and GGUF models in `models/` (see `engine/llm.py` CATALOGUE), then:
  - `python -I server.py --model qwen2.5-1.5b`
  - `python -I server.py --team qwen2.5-0.5b,qwen2.5-1.5b` (small model picks, large model writes)
