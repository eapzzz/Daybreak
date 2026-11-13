package daybreak.android;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioManager;
import android.os.Build;
import android.util.Log;

final class AlarmSchedule {
    static final String PREPARE = "daybreak.android.PREPARE";
    static final String EXPIRE = "daybreak.android.EXPIRE";
    static final String SILENCE = "daybreak.android.SILENCE";
    static final long INTERVAL = 30_000;
    static final long LEAD = 2_000;
    static final long LATE_LIMIT = 2_500;
    private static final String TAG = "DaybreakAlarm";
    private final Context context;
    private final SharedPreferences state;
    private final AlarmManager alarms;
    private final AudioManager audio;

    AlarmSchedule(Context context) {
        this.context = context.getApplicationContext();
        state = this.context.getSharedPreferences("daybreak", Context.MODE_PRIVATE);
        alarms = this.context.getSystemService(AlarmManager.class);
        audio = this.context.getSystemService(AudioManager.class);
    }

    String session() {
        return state.getString("session", "");
    }

    boolean active() {
        return active(session());
    }

    boolean active(String session) {
        return session != null && !session.isEmpty() && session.equals(session())
                && System.currentTimeMillis() < state.getLong("expires", 0);
    }

    boolean exactAllowed() {
        return Build.VERSION.SDK_INT < 31 || alarms.canScheduleExactAlarms();
    }

    void arm(String session, long start, long expires) {
        if (!exactAllowed()) {
            Log.w(TAG, "Exact alarm permission is required");
            return;
        }
        long oldExpiry = state.getLong("expires", 0);
        boolean same = session.equals(session()) && start == state.getLong("start", 0)
                && active(session);
        if (!same) stop();
        state.edit().putString("session", session).putLong("start", start)
                .putLong("expires", expires).commit();
        long previous = state.getLong("next_prepare", 0);
        if (!same || previous + LEAD >= oldExpiry || previous < System.currentTimeMillis() - LATE_LIMIT) {
            planPreparation(session, System.currentTimeMillis());
        }
        schedule(EXPIRE, 3, expires, session, expires);
        if (!same) Log.i(TAG, "Armed for " + start);
    }

    void restore() {
        if (!active() || !exactAllowed()) {
            stop();
            return;
        }
        planPreparation(session(), System.currentTimeMillis());
        long expires = state.getLong("expires", 0);
        schedule(EXPIRE, 3, expires, session(), expires);
    }

    private void planPreparation(String session, long after) {
        long start = state.getLong("start", 0);
        long offset = Math.max(0, (after + LEAD - start + INTERVAL - 1) / INTERVAL);
        long due = start + offset * INTERVAL;
        state.edit().putLong("next_prepare", due - LEAD).commit();
        if (due < state.getLong("expires", 0)) {
            schedule(PREPARE, 1, due - LEAD, session, due);
        }
    }

    void prepare(String session, long due) {
        if (!active(session) || due - LEAD != state.getLong("next_prepare", 0)) return;
        long now = System.currentTimeMillis();
        if (now < due) {
            try {
                context.startForegroundService(new Intent(context, RingService.class)
                        .putExtra("session", session).putExtra("due", due));
            } catch (IllegalStateException | SecurityException error) {
                Log.w(TAG, "Android could not start the alarm service", error);
            }
        } else {
            Log.w(TAG, "Preparation arrived after its alarm time");
        }
        planPreparation(session, Math.max(now, due + 1));
    }

    void prepareVolume(long due) {
        if (!state.contains("volume")) {
            state.edit().putInt("volume", audio.getStreamVolume(AudioManager.STREAM_ALARM)).commit();
        }
        int maximum = audio.getStreamMaxVolume(AudioManager.STREAM_ALARM);
        try {
            audio.setStreamVolume(AudioManager.STREAM_ALARM, maximum, 0);
        } catch (SecurityException error) {
            Log.w(TAG, "Android refused the alarm volume change", error);
        }
        Log.i(TAG, "Prepared " + due + " at " + System.currentTimeMillis()
                + " alarmVolume=" + audio.getStreamVolume(AudioManager.STREAM_ALARM));
    }

    boolean claimRing(String session, long due) {
        long now = System.currentTimeMillis();
        if (!active(session) || now < due || now - due > LATE_LIMIT
                || due <= state.getLong("last_ring", 0)) return false;
        state.edit().putLong("last_ring", due).commit();
        Log.i(TAG, "Ring " + due + " at " + now);
        return true;
    }

    void silence(String session) {
        if (session != null && session.equals(session())) RingService.silenceCurrent(session);
    }

    void expire(String session, long expires) {
        if (session != null && session.equals(session()) && expires == state.getLong("expires", 0)
                && System.currentTimeMillis() >= expires) stop();
    }

    void stop() {
        for (int id : new int[]{1, 3}) {
            String action = id == 1 ? PREPARE : EXPIRE;
            PendingIntent operation = PendingIntent.getBroadcast(context, id,
                    new Intent(context, AlarmReceiver.class).setAction(action),
                    PendingIntent.FLAG_NO_CREATE | PendingIntent.FLAG_IMMUTABLE);
            if (operation != null) {
                alarms.cancel(operation);
                operation.cancel();
            }
        }
        if (state.contains("volume")) {
            try {
                audio.setStreamVolume(AudioManager.STREAM_ALARM, state.getInt("volume", 0), 0);
            } catch (SecurityException error) {
                Log.w(TAG, "Android refused to restore the alarm volume", error);
            }
        }
        context.stopService(new Intent(context, RingService.class));
        if (!session().isEmpty()) Log.i(TAG, "Series stopped");
        state.edit().remove("session").remove("start").remove("expires").remove("volume")
                .remove("next_prepare").remove("last_ring").commit();
    }

    private void schedule(String action, int id, long trigger, String session, long time) {
        Intent event = new Intent(context, AlarmReceiver.class).setAction(action)
                .putExtra("session", session).putExtra("time", time);
        PendingIntent operation = PendingIntent.getBroadcast(context, id, event,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent show = PendingIntent.getActivity(context, 0,
                new Intent(context, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        alarms.setAlarmClock(new AlarmManager.AlarmClockInfo(trigger, show), operation);
    }
}
