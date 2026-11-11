# Security

## Reporting a problem

Please report security problems privately, through GitHub's
[private vulnerability reporting](https://github.com/ArmanAhmed00/Personalized_Rag-Chat_System/security/advisories/new),
not in a public issue. Include the version, how you run Outlawz, and steps to reproduce. You'll
get a reply within a week, and a fix or a plan within 30 days for confirmed problems. Credit is
given in the release notes unless you prefer otherwise.

Only the latest release gets security fixes.

## How Outlawz protects you

Outlawz is a local app: one person, one computer. Its defences assume a web browser that also
visits other, possibly hostile, websites, and documents that may have been crafted to attack it.

**Network**
- Listens on 127.0.0.1 by default. Requests from other addresses are refused unless an access
  token is set (`OUTLAWZ_ACCESS_TOKEN`); then every API request must carry it, compared in
  constant time. Use HTTPS (a reverse proxy) when exposing it beyond your computer.
- Requests with an unknown `Host` header are refused (protects against DNS rebinding).
- Every state-changing request needs the `X-Outlawz` header, which a cross-site form or image
  can't send, and cross-origin requests are refused unless an origin is explicitly allowed for
  development.
- The web app is served with a strict Content-Security-Policy: scripts only from the app itself
  plus the exact inline scripts of the build (by SHA-256 hash), no `eval`, no framing. Original
  documents are served with `default-src 'none'`.

**Files**
- Every path is resolved and checked to stay inside the library (no `..`, no links out).
- Uploads are size-limited, sniffed by content (not by extension), validated by the parser, and
  saved under a safe name. Hidden folders can't be written to.
- Writes are atomic (temporary file, fsync, rename), and a single-instance lock stops two copies
  of Outlawz from writing the same library.
- Backups carry a SHA-256 manifest. A restore verifies every file and refuses archive entries that
  would land outside the target before anything is written.

**Models and data**
- Model output is untrusted: citations are checked against the passages that were actually sent,
  analysis plans must match a fixed menu (the model never writes or runs code), and computed
  summaries are checked so they contain no number that wasn't computed.
- Document text is treated as data in prompts, and the answer is rendered as text, never as HTML.
- API keys are stored only in `secrets.json` in the OS data folder (readable by your user only on
  macOS and Linux). They're never sent to the browser, written to the library, included in
  exports or backups, or shown in logs. Credentials in model URLs are hidden in messages.
- Logs, metrics, traces and the activity log hold numbers and ids only. The activity log is
  hash-chained, so a change to a past event can be detected.
- No telemetry. The only outgoing connections are to the model you chose and, if you turn it on,
  a daily version check with pypi.org.

**Supply chain**
- CI scans every commit for secrets (gitleaks), and dependency updates are proposed monthly.
- Releases are published to PyPI with trusted publishing (no long-lived token), and the Docker
  image runs as a non-root user.
