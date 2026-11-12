package daybreak.android;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class AlarmReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        AlarmSchedule schedule = new AlarmSchedule(context);
        String action = intent.getAction();
        if (Intent.ACTION_BOOT_COMPLETED.equals(action) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            schedule.restore();
            return;
        }
        String session = intent.getStringExtra("session");
        long time = intent.getLongExtra("time", 0);
        if (AlarmSchedule.PREPARE.equals(action)) {
            schedule.prepare(session, time);
        } else if (AlarmSchedule.EXPIRE.equals(action)) {
            schedule.expire(session, time);
        } else if (AlarmSchedule.SILENCE.equals(action)) {
            schedule.silence(session);
        }
    }
}
