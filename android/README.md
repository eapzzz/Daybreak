# Daybreak for Android

Install the signed APK and open Daybreak once. Allow exact alarms, notifications
and full-screen alarms using the buttons on its setup screen. The local phone
test runs for 30 seconds. It only tests the app itself.

To enable daily wake-ups, use **Sound** in the server's `daybreak` console.

1. Select **Show pairing key** and save the key in the Android app. Keep it private.
2. Lock the phone and select **Test Android link** in the server console. The first
   ring comes about 12 seconds later. Tap **Silence this ring**, let the next one
   ring, and scan the paired tag. Verify that audio stops and alarm volume returns
   to its previous level.
3. Select **Enable 10-second mode** after the test succeeds. The same Home
   Assistant tag and phone pairing still finish the daily wake-up.

The app uses Android's default alarm sound. Android must allow alarms through the
current Do Not Disturb mode. The app raises the alarm stream, which is separate
from ringtone and media volume.

## Build

Install JDK 17 or newer and Android SDK Platform 34 and Build Tools 36.0.0.
Set `ANDROID_SDK_ROOT` to the SDK directory and run
`bash android/build.sh /absolute/path/daybreak-unsigned.apk` from the repository.
Sign the result using the SDK's `apksigner`. Use the same private signing key for
future updates to the installed app.

A system alarm wakes the app for preparation. A foreground service then raises
alarm volume and schedules playback for the remaining two seconds. This keeps
the final two seconds on-device, including on Android versions that enforce a
five-second minimum for new system alarms.
