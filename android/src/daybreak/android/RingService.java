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
import android.util.Log;

import java.io.IOException;

public final class RingService extends Service {
    private static final String RUNNING_CHANNEL = "daybreak-session";
    private static final String RING_CHANNEL = "daybreak-alarm";
    private static final int RUNNING_NOTIFICATION = 1;
    private static final int RING_NOTIFICATION = 2;
    private static RingService current;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private AlarmSchedule schedule;
    private NotificationManager notifications;
    private PowerManager.WakeLock preparing;
    private MediaPlayer player;
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
            handler.removeCallbacksAndMessages(null);
            preparing.acquire(5_000);
            handler.postDelayed(() -> {
                try {
                    if (schedule.claimRing(requested, due)) play(requested);
                } finally {
                    releasePreparationLock();
                }
            }, Math.max(0, due - System.currentTimeMillis()));
        }
        return START_NOT_STICKY;
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
                    ready.start();
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
        handler.removeCallbacksAndMessages(null);
        releasePreparationLock();
        releasePlayer();
        stopForeground(STOP_FOREGROUND_REMOVE);
        current = null;
        super.onDestroy();
    }

    private void releasePlayer() {
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
