# Daybreak Implementation Plan

**Goal:** deliver a Git-ready, self-contained service and SSH console.
**Architecture:** pure schedule/config/network model, durable SQLite coordinator,
authenticated HA adapter, local control socket, full-screen terminal client.
**Tech stack:** Python 3.10+, aiohttp, prompt_toolkit, sqlite3, systemd, pytest.
**Spec:** DESIGN.md

## Tasks and verification
- [x] Pure model (`model.py`, `tests/test_model.py`): exact times, repeat cadence, daily
  overrides, disabled weekdays, DST wall times, normalization and Home/Away/Unknown.
  Verify `pytest tests/test_model.py` before moving on.
- [x] Store and transport (`store.py`, `ha.py`, respective tests): transactional
  completion/cleanup, no credentials in logs, real loopback HTTP/WebSocket test.
- [x] Coordinator (`engine.py`, `tests/test_engine.py`): configurable preparation (default 10 seconds),
  evening seed, Tag ordering, scope isolation, cancellation on network change,
  config replacement, restart recovery, test expiry, no stale alarm replay.
- [x] Local API (`service.py`, `tests/test_service.py`): restricted Unix socket,
  settings validation, serialized changes, diagnostics and daemon heartbeat.
- [x] Console (`tui.py`, `tests/test_tui.py`): all pages, editable forms, picker,
  preview, live state, tests, Tag capture, errors, backup/restore, system actions.
  Render real app through virtual terminal and export a demo screenshot.
- [x] Deploy/docs (`install.sh`, `system.py`, README): isolated staging, protected
  files, no secrets in repo, Git workflow and in-console install/update paths.
- [x] Run full suite, compile sources, check shell syntax, audit archive and add
  exact results to TEST-REPORT.md; commit and package with no .git or runtime data.
