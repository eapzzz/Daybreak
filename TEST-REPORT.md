# Daybreak — local verification report

Date: 2026-09-16

## Environment

- Linux container, Python 3.13.5.
- aiohttp 3.13.3; prompt_toolkit 3.0.52.
- pytest 9.0.2; pytest-asyncio 1.3.0.
- Tests use temporary directories, synthetic network identifiers and fake HA
  credentials. No user's Home Assistant or phone was accessed.

## Results

```text
python -m pytest -q
100 passed in 6.43s

python -m compileall -q src
PASS

bash -n install.sh
PASS

python -m pip wheel --no-deps --no-build-isolation .
PASS — installable daybreak_wake wheel built
```

The built wheel was installed into a temporary environment. Package import,
07:10 → 08:30 scheduling and composition of all nine console views passed using
the already-available pinned dependencies. This is not a test of downloading
packages from PyPI on the target server.

The systemd unit's directives passed `systemd-analyze verify` using a temporary
copy whose ExecStart points to the existing test interpreter. The unmodified
unit cannot be activated here because `/opt/daybreak/current` is an installation
path on the target host, and this container is not booted with systemd.

## What the tests exercise

- Exact first times, rounded ends, dense/sparse phases, one-date overrides,
  disabled weekdays, midnight crossings and DST wall-time resolution.
- Home/Away/Unknown rules, BSSID normalization, optional heartbeat expiry and
  unchanged Wi-Fi reports without artificial timestamp freshness.
- Preparation at T−10 seconds only; no full-minute volume tick; no past alarm
  replay, duplicate restart preparation or unbounded late volume retries.
- QR arriving during a command, network changes during a command, durable
  completion, cleanup retries, daytime volume and recovery of old pending work.
- Evening seed under the home rule, reconciliation after a changed plan,
  return-home behavior, explicit recording of skipped-away mornings.
- Separate rolling/network tests, first-run unarmed behavior and acceptance
  only after a user-confirmed live test with no pending cleanup.
- Real loopback HTTP and WebSocket transport: authentication, service payloads,
  subscription events, out-of-order state protection, state removal, redacted
  failures and blocked HTTP redirects.
- Integrated Engine + HA HTTP/WebSocket + Unix control API, including QR, Away
  and Unknown transitions after a prepared alarm and durable cleanup state.
- The actual daemon as a subprocess: initial socket readiness, a separate
  console client, single-instance protection and clean SIGTERM shutdown.
- Settings privacy, atomic writes, non-token backups, validation and control
  action allowlists.
- The actual full-screen console in a virtual terminal: all nine pages, F-key
  navigation, searchable picker selection, modal Escape, form submission,
  password masking, 07:10 preview including the last alarm, compact 100×30 layout.

Screenshots in `docs/` were produced from the running console's screen buffer
with synthetic demonstration data and visually inspected. They are not a claim
that the phone test ran against a physical device.

## Remaining target-device checks

Not run in this environment:

1. Full root installation/rollback and systemd activation on the user's host.
2. HA/Companion/Google Clock execution on the user's actual phone, including
   cancellation when its reported network changes.
3. A complete overnight run with the phone asleep.
4. GitHub-hosted CI on Python 3.10, 3.11 and 3.12. A workflow is included, not
   a claim that those jobs have already executed.

Complete Connection, Home Wi-Fi and both live tests in the console before
arming the routine. API acceptance is not physical-phone confirmation.

## NFC migration update — 2026-11-06 (1.1.0)

Local verification on Linux / Python 3.14.8, using isolated dependencies:

```text
python -m pytest -q
109 passed in 5.43s
bash -n install.sh
PASS
```

Added checks cover NFC pairing capture/cancel/timeout, explicit save, replacement
of the old tag, wrong-phone rejection, persistence across restart, preservation of
legacy settings/history/status, rejection during an active wake-up or live test,
and mandatory retesting after changed identifiers. A completed test from before
the tag change cannot re-confirm the replacement. The real TUI is rendered and
checked for NFC controls. Existing transport/scheduler tests remain in the suite.

These local tests use synthetic identifiers and fake HA/phone transports. Physical
NTAG213 reading, alarm cancellation, Android lock-screen behavior and the first
real morning still require the user's phone. The earlier verification record
above describes 1.0 and is retained as historical evidence.

## Evening alarm lifecycle — 2026-11-06 (1.1.1)

`python -m pytest -q`: **112 passed in 6.02s** (same isolated local environment).
New tests cover evening-to-morning preparation without a second SET_ALARM,
restart persistence, NFC cleanup, sound-command retry without re-creation, and
retrying an evening creation that was not acknowledged. These tests verify the
commands and durable ledger, not the private storage of the phone's Clock app.

## One-second preparation — 2026-11-06 (1.1.2)

`python -m pytest -q`: **116 passed in 5.86s**. Added regression checks for no
commands before T−1, alarm and sound dispatch at T−1, shrinking HTTP budgets,
no late sound after a slow creation, and NFC priority during one-second dispatch.
Existing timing tests explicitly retain their 10-second fixtures; new installs
default to one second and saved configurations remain readable. Actual phone
delivery within one second is not established by the synthetic tests.
