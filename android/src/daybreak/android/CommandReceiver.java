package daybreak.android;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.Intent;
import android.util.Base64;
import android.util.Log;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public final class CommandReceiver extends BroadcastReceiver {
    static final String ACTION = "daybreak.android.CONTROL";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!ACTION.equals(intent.getAction())) return;
        String token = intent.getStringExtra("token");
        if (token == null) return;
        try {
            apply(context, token);
        } catch (GeneralSecurityException | IllegalArgumentException | SecurityException error) {
            Log.w("DaybreakAlarm", "Invalid or unavailable alarm command");
        }
    }

    private void apply(Context context, String token) throws GeneralSecurityException {
        if (token.length() > 384) return;
        String[] parts = token.split("\\.", -1);
        if (parts.length != 2) return;
        SharedPreferences pairing = context.getSharedPreferences("pairing", Context.MODE_PRIVATE);
        String key = pairing.getString("key", "");
        if (key.isEmpty()) return;
        byte[] message = Base64.decode(parts[0], Base64.URL_SAFE | Base64.NO_WRAP);
        byte[] signature = Base64.decode(parts[1], Base64.URL_SAFE | Base64.NO_WRAP);
        Mac hmac = Mac.getInstance("HmacSHA256");
        hmac.init(new SecretKeySpec(unhex(key), "HmacSHA256"));
        if (!MessageDigest.isEqual(hmac.doFinal(message), signature)) return;

        String[] command = new String(message, StandardCharsets.US_ASCII).split("\\|", -1);
        if (command.length != 6 || !"1".equals(command[0])
                || !command[2].matches("[A-Za-z0-9:_-]{1,40}")) return;
        long start = Long.parseLong(command[3]);
        long issued = Long.parseLong(command[4]);
        long expires = Long.parseLong(command[5]);
        long now = System.currentTimeMillis();
        if (issued <= pairing.getLong("last", 0) || issued < now - 30_000 || issued > now + 10_000) return;
        AlarmSchedule schedule = new AlarmSchedule(context);
        if ("run".equals(command[1]) && expires > now && expires <= issued + 90_000
                && start > 0 && start < expires) {
            schedule.arm(command[2], start, expires);
        } else if ("stop".equals(command[1])) {
            if (command[2].equals(schedule.session())) schedule.stop();
        } else {
            return;
        }
        pairing.edit().putLong("last", issued).commit();
    }

    private static byte[] unhex(String value) {
        byte[] bytes = new byte[value.length() / 2];
        for (int i = 0; i < bytes.length; i++) {
            bytes[i] = (byte) Integer.parseInt(value.substring(i * 2, i * 2 + 2), 16);
        }
        return bytes;
    }
}
