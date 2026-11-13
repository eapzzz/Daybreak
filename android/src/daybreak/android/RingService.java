package daybreak.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import java.io.IOException;

public final class RingService extends Service {
    private static final String RUNNING_CHANNEL = "daybreak-session";
    private static final String RING_CHANNEL = "daybreak-alarm";
    private static final int RUNNING_NOTIFICATION = 1;
    private static final int RING_NOTIFICATION = 2;
    private static final long FADE_DURATION_MS = 8_000;
    private static final long FADE_TICK_MS = 50;
    private static final float FADE_MIN_VOLUME = 0.02f;
    private static RingService current;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private AlarmSchedule schedule;
    private NotificationManager notifications;
    private PowerManager.WakeLock preparing;
    private MediaPlayer player;
    private Runnable pendingRing;
    private Runnable fadeStep;
    private String session;

    @Override
    public void onCreate() {
        super.onCreate();
        current = this;
        schedule = new AlarmSchedule(this);
        notifications = getSystemService(NotificationManager.class);
        preparing = getSystemService(PowerManager.class).newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK, "Daybreak:preparing");
        preparing.setReferenceCounted(false);
        channel(RUNNING_CHANNEL, "Daybreak session", NotificationManager.IMPORTANCE_LOW);
        channel(RING_CHANNEL, "Daybreak alarms", NotificationManager.IMPORTANCE_HIGH);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String requested = intent == null ? null : intent.getStringExtra("session");
        if (!schedule.active(requested)) {
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        if (!requested.equals(session)) {
            releasePlayer();
            session = requested;
            Notification.Builder preparingNotification = builder(RUNNING_CHANNEL)
                    .setContentText("Wake-up active until the tag is scanned");
            if (Build.VERSION.SDK_INT >= 31) {
                preparingNotification.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
            }
            startForeground(RUNNING_NOTIFICATION, preparingNotification.build());
        }
        long due = intent.getLongExtra("due", 0);
        long delay = due - System.currentTimeMillis();
        if (delay >= -AlarmSchedule.LATE_LIMIT && delay <= AlarmSchedule.LEAD) {
            schedule.prepareVolume(due);
            cancelPendingRing();
            preparing.acquire(5_000);
            ringWhenDue(requested, due);
        }
        return START_NOT_STICKY;
    }

    private void ringWhenDue(String requested, long due) {
        long delay = due - System.currentTimeMillis();
        // Handler runs on uptime, while the alarm grid uses wall time.
        if (delay > 0 && delay <= AlarmSchedule.LEAD) {
            pendingRing = () -> {
                pendingRing = null;
                ringWhenDue(requested, due);
            };
            handler.postDelayed(pendingRing, delay);
            return;
        }
        pendingRing = null;
        try {
            if (schedule.claimRing(requested, due)) play(requested);
        } finally {
            releasePreparationLock();
        }
    }

    private void play(String requested) {
        if (player != null) return;
        notifications.notify(RING_NOTIFICATION, ringNotification(requested));
        Uri sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        try {
            MediaPlayer next = new MediaPlayer();
            player = next;
            next.setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            next.setWakeMode(this, PowerManager.PARTIAL_WAKE_LOCK);
            next.setDataSource(this, sound);
            next.setLooping(true);
            next.setOnPreparedListener(ready -> {
                if (ready == player && schedule.active(requested)) {
                    ready.setVolume(FADE_MIN_VOLUME, FADE_MIN_VOLUME);
                    ready.start();
                    startFadeIn(ready);
                    Log.i("DaybreakAlarm", "Audio playing");
                }
            });
            next.setOnErrorListener((broken, what, extra) -> {
                Log.e("DaybreakAlarm", "Alarm playback failed: " + what + "/" + extra);
                releasePlayer();
                return true;
            });
            next.prepareAsync();
        } catch (IOException | RuntimeException error) {
            Log.e("DaybreakAlarm", "Cannot play the selected alarm tone", error);
            releasePlayer();
        }
    }

    private static float fadeVolume(float progress) {
        if (progress >= 1f) return 1f;
        if (progress <= 0f) return FADE_MIN_VOLUME;
        return (float) Math.pow(FADE_MIN_VOLUME, 1.0 - progress);
    }

    private void startFadeIn(MediaPlayer target) {
        cancelFade();
        final long startedAt = SystemClock.elapsedRealtime();
        fadeStep = new Runnable() {
            @Override
            public void run() {
                if (target != player || fadeStep != this) return;
                float progress = Math.min(1f, Math.max(0f,
                        (float) (SystemClock.elapsedRealtime() - startedAt) / FADE_DURATION_MS));
                float volume = fadeVolume(progress);
                try {
                    target.setVolume(volume, volume);
                } catch (IllegalStateException error) {
                    cancelFade();
                    return;
                }
                if (progress < 1f) {
                    handler.postDelayed(this, FADE_TICK_MS);
                } else {
                    fadeStep = null;
                }
            }
        };
        handler.postDelayed(fadeStep, FADE_TICK_MS);
    }

    private void cancelPendingRing() {
        if (pendingRing != null) {
            handler.removeCallbacks(pendingRing);
            pendingRing = null;
        }
    }

    private void cancelFade() {
        if (fadeStep != null) {
            handler.removeCallbacks(fadeStep);
            fadeStep = null;
        }
    }

    private void channel(String id, String label, int importance) {
        NotificationChannel channel = new NotificationChannel(id, label, importance);
        channel.setSound(null, null);
        notifications.createNotificationChannel(channel);
    }

    private Notification.Builder builder(String channel) {
        return new Notification.Builder(this, channel)
                .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                .setContentTitle("Daybreak")
                .setCategory(Notification.CATEGORY_ALARM)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true);
    }

    private Notification ringNotification(String session) {
        PendingIntent screen = PendingIntent.getActivity(this, 4,
                new Intent(this, RingActivity.class).putExtra("session", session),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent silence = PendingIntent.getBroadcast(this, 5,
                new Intent(this, AlarmReceiver.class).setAction(AlarmSchedule.SILENCE).putExtra("session", session),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return builder(RING_CHANNEL)
                .setContentText("Scan the wake-up tag to finish")
                .setContentIntent(screen)
                .setFullScreenIntent(screen, true)
                .addAction(new Notification.Action.Builder(null, "Silence", silence).build())
                .build();
    }

    static void silenceCurrent(String session) {
        if (current != null && session.equals(current.session)) {
            current.releasePlayer();
            Log.i("DaybreakAlarm", "Silenced one ring");
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        cancelPendingRing();
        releasePreparationLock();
        releasePlayer();
        stopForeground(STOP_FOREGROUND_REMOVE);
        current = null;
        super.onDestroy();
    }

    private void releasePlayer() {
        cancelFade();
        if (player != null) {
            player.release();
            player = null;
        }
        notifications.cancel(RING_NOTIFICATION);
    }

    private void releasePreparationLock() {
        if (preparing.isHeld()) preparing.release();
    }
}
