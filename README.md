# Sidelamp

Chat with your own documents on your own computer. Sidelamp reads PDFs, EPUB ebooks and CSV
spreadsheets, answers questions using only those documents, and links every claim to the exact
passage behind it, down to the page. For spreadsheets it computes totals, comparisons and charts
with real code (pandas) instead of letting the model guess numbers.

- **Local-first and private.** No accounts, no cloud services, no telemetry. Documents are
  ordinary files in a folder you choose. With the default local model, nothing leaves your computer.
- **Easy to install** on macOS, Windows and Linux, on ordinary laptops (8 GB RAM, no GPU).
- **Easy to contribute to.** Readable code, offline tests, and small documented extension points.

By Arman Ahmed. MIT licence.

> **Status:** under active development. See [docs/plan.md](docs/plan.md) for the build plan.

## Developing

```bash
# Backend (Python 3.11+)
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest              # runs offline: temp folders, a fake model and a fake embedder
ruff check . && ruff format --check . && mypy

# Frontend (Node 20+)
cd frontend
npm ci
npm run lint && npm run typecheck && npm run build
```
