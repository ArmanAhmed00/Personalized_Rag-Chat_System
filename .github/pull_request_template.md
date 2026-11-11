## What this changes

<!-- One or two sentences. Link the issue it fixes, if any: "Fixes #123". -->

## How it was checked

- [ ] `pytest`, `ruff check . ../scripts`, `ruff format --check . ../scripts` and `mypy` pass in `backend/`
- [ ] `npm run lint`, `npm run typecheck` and `npm run build` pass in `frontend/` (if the web app changed)
- [ ] New behaviour has a test (offline: temp folders and the fake model)
- [ ] UI text lives in `frontend/lib/strings.ts` and is plain language
- [ ] Logs and metrics still hold numbers and ids only (no document text, questions or answers)
- [ ] Files on disk are still written atomically, and the on-disk format version was bumped with a migration if it changed
- [ ] `CHANGELOG.md` has a line under "Unreleased"
