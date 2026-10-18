# Mhizva

A local, lightweight AI that answers questions from your own documents. Built for people with no tech knowledge: drop files in, ask in plain words, get an answer with its source.

**MDTree** is its retrieval engine. Documents are stored as markdown in a folder tree where every folder has an `index.md`. A question descends the tree one small index at a time, splitting a reading budget across likely branches (flow-based branching), so a small local model with a 4k context can search a large collection.

## Layout
- `engine/`: MDTree (tree storage, filing, flow search, model client)
- `server.py`: local server for the UI and the JSON API on http://127.0.0.1:8765
- `ui/`: the app (plain HTML/CSS/JS, works offline)
- `tests/`: engine tests (`python -I tests/test_engine.py`, `python -I tests/test_budget.py`)
- `build_bitnet.ps1`: builds BitNet's llama-server on Windows (VS 2022 Build Tools + Clang)

## Run
- Without a model (keyword stand-in): `python -I server.py --fake`
- With BitNet: clone https://github.com/microsoft/BitNet into `BitNet/`, download `microsoft/BitNet-b1.58-2B-4T-gguf` into `BitNet/models/BitNet-b1.58-2B-4T/`, run `build_bitnet.ps1`, then `python -I server.py`
