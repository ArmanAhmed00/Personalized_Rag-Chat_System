# Roadmap

What's likely next, roughly in order. Nothing here is promised; ideas and help are welcome
(open an issue to discuss before starting on a big one).

## Soon

- **More file types:** Markdown and plain text, Word (`.docx`), HTML, and Excel sheets as tables.
  Each is one extractor (see `ingest/extractors/`).
- **Translations:** all UI copy is already in `frontend/lib/strings.ts`; next is a language picker
  and a first translation.
- **Better first run on slow computers:** pick a smaller model automatically when memory is low,
  and show download progress for the search model.
- **Dark mode**, with chart colours checked against the dark background.

## Later

- **Desktop wrapper** (a small native window and a tray icon) for people who don't want a terminal.
- **Several libraries** switchable from the app, each with its own settings.
- **Richer citations:** highlight the cited sentence inside the PDF page view.
- **More analyses:** time-series trends and seasonality, and joins across two CSVs on a shared
  column.
- **Answer quality:** a larger public benchmark, and per-model recommendations based on it.

## Not planned

- Accounts, a hosted version, or telemetry. Outlawz stays a local, private tool.
- A database server. Files you can read and back up are a feature.
