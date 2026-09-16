# Primary references

Consulted while building this repository (2026-09-16):

- [Home Assistant WebSocket API](https://developers.home-assistant.io/docs/api/websocket/): authentication, event subscriptions and state changes.
- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/): state snapshots, service discovery and service calls.
- [Home Assistant authentication](https://developers.home-assistant.io/docs/auth_api/): long-lived access tokens.
- [Companion sensors](https://companion.home-assistant.io/docs/core/sensors/): Wi-Fi connection/BSSID reports.
- [Companion notification commands](https://companion.home-assistant.io/docs/notifications/notification-commands/): volume commands, sensor refresh, typed intent extras.
- [Companion local push](https://companion.home-assistant.io/docs/notifications/notification-local/): persistent connection behavior.
- [Android AlarmClock](https://developer.android.com/reference/android/provider/AlarmClock): SET_ALARM and DISMISS_ALARM, exact-label search.
- [prompt_toolkit full-screen applications](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/full_screen_apps.html): layouts, key bindings, styles and async apps.
- [prompt_toolkit unit testing](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/advanced_topics/unit_testing.html): pipe input and dummy output testing.
- [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html): service lifecycle and restart semantics.

The app implements its own scheduling and persistence. These sources document
its external interfaces, not certification of this application.
