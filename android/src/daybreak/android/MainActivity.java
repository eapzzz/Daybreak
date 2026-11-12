package daybreak.android;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private AlarmSchedule schedule;
    private SharedPreferences pairing;
    private TextView status;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        schedule = new AlarmSchedule(this);
        pairing = getSharedPreferences("pairing", Context.MODE_PRIVATE);

        ScrollView scroll = new ScrollView(this);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        page.setPadding(padding, padding, padding, padding);
        scroll.addView(page);
        setContentView(scroll);

        TextView title = new TextView(this);
        title.setText("Daybreak");
        title.setTextSize(32);
        page.addView(title);
        status = new TextView(this);
        status.setPadding(0, padding, 0, padding);
        page.addView(status);
        if (Build.VERSION.SDK_INT >= 31) {
            button(page, "Allow exact alarms", () -> startActivity(new Intent(
                    Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, packageUri())));
        }
        if (Build.VERSION.SDK_INT >= 33) {
            button(page, "Allow alarm notifications", () -> {
                if (notificationsAllowed()) {
                    startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
                } else {
                    requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
                }
            });
        }
        if (Build.VERSION.SDK_INT >= 34) {
            button(page, "Allow alarms on the lock screen", () -> startActivity(new Intent(
                    Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, packageUri())));
        }
        EditText key = new EditText(this);
        key.setHint("Pairing key from Daybreak → Sound");
        key.setSingleLine(true);
        key.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        page.addView(key);
        button(page, "Save pairing key", () -> {
            if (schedule.active()) {
                status.setText("Wait for the current series to finish.");
                return;
            }
            String value = key.getText().toString().trim();
            if (!value.matches("[0-9a-fA-F]{64}")) {
                key.setError("Enter the 64-character key from Daybreak.");
                return;
            }
            value = value.toLowerCase(java.util.Locale.ROOT);
            if (!value.equals(pairing.getString("key", ""))) {
                pairing.edit().putString("key", value).remove("last").commit();
            }
            key.setText("");
            updateStatus();
        });
        button(page, "Run 30-second phone test", () -> {
            if (!schedule.exactAllowed() || !notificationsAllowed() || schedule.active()) {
                updateStatus();
                return;
            }
            long start = System.currentTimeMillis() + 5_000;
            schedule.arm("local-test", start, start + 30_000);
            updateStatus();
        });
        button(page, "Stop phone test", () -> {
            if ("local-test".equals(schedule.session())) schedule.stop();
            updateStatus();
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateStatus();
    }

    private Uri packageUri() {
        return Uri.parse("package:" + getPackageName());
    }

    private boolean notificationsAllowed() {
        return getSystemService(NotificationManager.class).areNotificationsEnabled()
                && (Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                        == PackageManager.PERMISSION_GRANTED);
    }

    private void updateStatus() {
        boolean fullScreen = Build.VERSION.SDK_INT < 34
                || getSystemService(NotificationManager.class).canUseFullScreenIntent();
        status.setText("Alarm every 10 seconds until Daybreak confirms your wake-up."
                + "\n\nExact alarms   " + (schedule.exactAllowed() ? "Allowed" : "Permission needed")
                + "\nNotifications   " + (notificationsAllowed() ? "Allowed" : "Permission needed")
                + "\nLock-screen alarms   " + (fullScreen ? "Allowed" : "Permission needed")
                + "\nServer key   " + (pairing.contains("key") ? "Saved" : "Missing")
                + (schedule.active() ? "\n\nAlarm series active." : ""));
    }

    private void button(LinearLayout page, String title, Runnable action) {
        Button button = new Button(this);
        button.setText(title);
        button.setOnClickListener(view -> action.run());
        page.addView(button);
    }
}
