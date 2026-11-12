package daybreak.android;

import android.app.Activity;
import android.media.AudioManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class RingActivity extends Activity {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private AlarmSchedule schedule;
    private String session;
    private final Runnable check = new Runnable() {
        @Override
        public void run() {
            if (!schedule.active(session)) finish();
            else handler.postDelayed(this, 500);
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        schedule = new AlarmSchedule(this);
        session = getIntent().getStringExtra("session");
        if (!schedule.active(session)) {
            finish();
            return;
        }
        setVolumeControlStream(AudioManager.STREAM_ALARM);
        setShowWhenLocked(true);
        setTurnScreenOn(true);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        setContentView(layout);

        TextView title = new TextView(this);
        title.setText("Daybreak");
        title.setTextSize(38);
        layout.addView(title);
        TextView hint = new TextView(this);
        hint.setText("Scan the wake-up tag to finish.");
        hint.setPadding(16, 20, 16, 20);
        layout.addView(hint);
        Button silence = new Button(this);
        silence.setText("Silence this ring");
        silence.setOnClickListener(view -> {
            schedule.silence(session);
            finish();
        });
        layout.addView(silence);
    }

    @Override
    protected void onStart() {
        super.onStart();
        handler.post(check);
    }

    @Override
    protected void onStop() {
        handler.removeCallbacks(check);
        super.onStop();
    }
}
