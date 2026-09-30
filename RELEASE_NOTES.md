# Release notes - reliability and performance pass

**Branch:** `perf/reliability-pass`
**Base:** `main` (`ea13333`)
**Scope:** 60 files, +4053 / -1943. Tests 18 -> 115.

A pass over the whole stack focused on three things: defects that only show up
under real load or on a non-Linux host, per-request work that does not need to
happen per request, and failure modes that were silent.

## Fixed

### Resource leaks
- **Session store leaked a file handle on every read.** `with sqlite3.connect()`
  commits but does not close. Replaced with a context manager that always closes,
  and added WAL plus a busy timeout. The `CREATE TABLE`/`CREATE INDEX` pair also
  ran on every single connection; it now runs once per process.
- **History queries scanned the whole table.** Every read filters and orders by
  `session_id`, which had no index. Added one on `(session_id, id)`.
- **`/ready` and the console endpoints opened a new HTTP client per service.**
  They now share one client and probe concurrently.

### Crashes on non-Docker hosts
- **Memory crashed at import** when `/data` was not writable, which is every host
  install outside a container. It now falls back to a temp directory, and a
  corrupt index starts empty instead of taking the service down.
- **Agent returned 500 for every tool decision** when the audit log was unwritable,
  for the same reason. Audit writes are now best-effort with a temp fallback, and a
  failed write is logged rather than raised.
- **Session store probed writability on every call.** A `mkdir`/`touch`/`unlink`
  round trip per read and write; now cached per directory.

### Incorrect behaviour
- **Routing matched on substrings.** "therapist" contains "api" and "explanation"
  contains "plan", so both went to the wrong expert. Patterns now match on word
  boundaries. Image generation additionally requires a creation verb *and* an
  image noun, so "create a summary" stays a chat turn.
- **The router mutated `os.environ` around an `await`** to reach a different
  backend, racing with any concurrent request reading the same variables.
  `complete()` now takes explicit `base_url` and `model` arguments.
- **A failed model load was reported as a successful swap.** `model_manager`
  discarded the `load()` response without checking it. It now validates and returns
  502/503.
- **A failed streaming fallback truncated the stream** with no terminal frame, so
  the client waited indefinitely. It now emits `event: error` then `[DONE]`.
- **A malformed router payload surfaced as a 500** instead of a 502, and was not
  counted as an error.
- **The document cap was broken.** Rebuilding the FAISS index called
  `IndexFlatIP.add()` with another index rather than an array.

### Reliability
- Router, memory, and expert-call failures now degrade (keyword routing, empty
  context) instead of failing the request.
- Concurrent model swaps are serialised, so two cannot unload each other's work.
- `model_manager` had no `/health` endpoint at all.

## Performance

| Area | Before | After |
| --- | --- | --- |
| Health probes | 8 sequential, up to ~16s when down | Concurrent, one timeout |
| Speech | Whisper weights reloaded per request | Loaded once, cached |
| Rate-limit map | Grew with every source IP | Pruned and capped |
| Attachment IO | Blocking reads on the event loop | Threaded, size-capped |
| Session reads | Full table scan, handle leak | Indexed, closed, WAL |
| Image generation | Base64 decode + re-encode | Pass-through with validation |

## Windows

`read_system()` read `/proc` exclusively, so CPU and memory rendered as `null` on
Windows even though the project targets it. Added a `ctypes` implementation and
made the Linux path threaded.

## Security

- API key comparison used `!=`, which leaks the key byte by byte. Now
  `secrets.compare_digest`, with public paths as an explicit allowlist.
- Requests are bounded at the schema: message length, attachment count, context
  items, document size, and audio/image bytes.
- Every environment variable is parsed through a helper that logs a bad value and
  falls back, instead of raising from inside a request handler.
- The container image ran as root. It now runs unprivileged, and the speech image
  installs `ffmpeg`, which faster-whisper needs to decode audio.

## Observability

- `/metrics` emitted no `HELP` or `TYPE` lines, leaving series unlabelled in
  Grafana. Added counters and gauges for both.
- Added `X-Request-ID` to every response and a matching correlation id in logs, so
  one request can be followed across services. The client's id is honoured if
  supplied.
- Added `GET /sessions`, `GET /sessions/{id}`, `DELETE /sessions/{id}`.

## Tooling, infra, and docs

- **`scripts/dev.py`** (`up` / `down` / `status`) and a real `Makefile`. The old
  `Makefile.txt` was WSL-only, started services with `&`, and left orphans with no
  way to stop them.
- **Compose was missing `model_manager` entirely.** Added it, plus healthcheck-gated
  dependencies, restart policies, and the full service URL map for the gateway.
- Replaced the `sas` placeholder with `info` / `env` / `run` / `check`.
- Added `.env.example`, `.gitattributes`, ruff and pytest config, and a CI workflow
  covering Linux and Windows.
- Removed 20 committed build-scratch files under `web/` and one stray `.bat` probe.
- Normalised mixed tabs/spaces and mixed line endings across the tree.
- Rewrote the README, which opened with a stray BOM and had no Windows instructions.

## Verification

- `pytest`: 115 passed (was 18).
- `ruff check` and `ruff format --check`: clean.
- Started all eleven services and exercised `/chat`, `/chat/stream`, memory
  add/search, session list/delete, `/ready`, `/metrics`, `/api/diagnostics`,
  `/api/activity` and the console. Shut the stack down cleanly afterwards.

## Known limitations

- **The Docker build was not exercised.** Docker is not installed on this machine,
  so `infra/Dockerfile` and the Compose files were validated structurally
  (YAML parses, all services and volumes resolve, no missing references) but not
  built or run. CI now runs `docker compose config` on every push to catch this.
- **No live model was available**, so inference paths were verified up to the
  backend call. The offline notice path, the streaming fallback, and the memory
  and session paths were all exercised end to end.
- **Experts still return honest offline notices** when no backend is configured.
  Connecting real models is unchanged from before and remains the next step.
