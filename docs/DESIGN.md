# Daybreak design

Approved behavior: an exact first alarm, at least 60 wall-clock minutes, with the
end rounded UP to a 30-minute boundary. Default 05:55 → 07:00; 07:10 → 08:30.
First 15 minutes every minute, then every 3 minutes and a final end alarm.
Prepare each alarm and volume only 10 seconds before it; no full-minute volume tick.
An evening seed uses the exact first alarm of the next scheduled day.

A dedicated daemon owns all scheduling, Wi-Fi decisions, secrets, SQLite history,
one-off plans and alarm cleanup. A separate full-screen terminal console talks to
it over a permission-restricted Unix socket. Closing SSH does not stop scheduling.
Python 3.10+, aiohttp and prompt_toolkit (the console has no browser dependency).

Home means HA is connected and the selected SSID AND selected BSSID match the
configured allowlists. Away and Unknown inhibit new alarms and loud commands.
Leaving Home cancels only Daybreak alarms, restores volume if we changed it,
and retains failed cleanup for retry. HA sensor timestamps are shown honestly;
REST fetching does NOT make an unchanged phone report fresh. An optional phone
heartbeat sensor can impose freshness without expiring unchanged Wi-Fi names.
No public IP rule. No secret or household identifiers in the repository.

QR matches tag and device IDs. It ends today's real session, not tomorrow's seed.
A test has an independent scope and never completes a real day. Completion and
cleanup survive restart. Commands are serialized; an incoming QR/network change
inhibits further preparation immediately, including a preparation in progress.
Never schedule an Android alarm after its deadline. Keep exact alarm labels in
SQLite before network I/O, for cleanup even after a timeout. API acceptance and
physical phone execution are distinct; acceptance is never called a phone ACK.

Console pages: Overview, Next wake-up, Routine (weekly schedule), Connection
(credentials/device/QR pairing and sensor discovery), Home Wi-Fi, Sound,
Diagnostics (real tests and network test), History, System (autostart, deploy
from Git checkout, backups, local support report). All edits validate before
commit. Per-day override expires with its chosen date, even if away. Config and
credentials live under /var/lib/daybreak, not in the Git checkout.

Deployment: staged virtualenv and source under /opt/daybreak; root installer,
unprivileged system service, root-operated console. Installation preserves an
old install until dependencies build successfully, then replaces the unit. New
scheduler remains unarmed until the console acceptance test is confirmed.
