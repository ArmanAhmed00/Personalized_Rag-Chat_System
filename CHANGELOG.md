# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

The first release.

### Added

- **Library:** PDF, EPUB and CSV files in a folder you choose, picked up automatically (new,
  changed, moved, deleted), with uploads, a Trash, re-reading and cancelling, and progress per
  document. Optional OCR for scanned PDFs with Tesseract. Sample documents to try.
- **Search:** hybrid BM25 and vector search with reciprocal rank fusion and reranking, with an
  explanation for every result.
- **Chat:** streamed answers that cite numbered passages down to the page, server-checked
  citations, a passage panel that opens the original document at the right page, follow-up
  questions, document selection, stop, feedback, rename, delete, and export as Markdown, JSON or
  PDF.
- **Preferences:** "Remember that…" suggestions you confirm, used as style guidance only.
- **Analysis:** CSV questions answered with a fixed menu of pandas analyses chosen by the model as
  JSON (totals, group comparisons with significance tests, relationships, correlations,
  distributions, crosstabs, outliers, missing values, summaries, rows); an automatic overview with
  findings; a form and JSON builder; and comparison of two CSVs. Every result has a chart, a table
  and a CSV download, and summaries contain only computed numbers.
- **Models:** Ollama (default), any OpenAI-compatible server, and Anthropic, with a test button
  and a visible notice whenever a cloud model is selected. FastEmbed for local search vectors.
- **System pages:** overview, metrics per route and step, sampled traces with waterfalls,
  processing status, a synthetic evaluation benchmark compared run to run, and a hash-chained
  activity log with CSV export. Logs hold numbers and ids only.
- **Data:** "Download everything", verified backups (manual and daily), restore with a safety
  backup, and `outlawz doctor`, `backup`, `verify`, `restore` and `eval` commands.
- **Security:** loopback-only by default, optional access token, Host and CSRF checks, strict
  Content-Security-Policy, path safety, atomic writes, a single-instance lock and a versioned
  on-disk format with migrations.
- **Packaging:** pipx-installable wheel with the web app built in, Docker image and Compose file,
  run scripts for macOS, Linux and Windows, and CI on all three.

[Unreleased]: https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System/releases/tag/v0.1.0
