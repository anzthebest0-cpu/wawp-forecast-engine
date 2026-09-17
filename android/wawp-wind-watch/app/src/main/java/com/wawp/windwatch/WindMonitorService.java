package com.wawp.windwatch;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.VibrationEffect;
import android.os.Vibrator;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

public class WindMonitorService extends Service {
    static final String ACTION_START = "com.wawp.windwatch.START";
    static final String ACTION_STOP = "com.wawp.windwatch.STOP";
    static final String ACTION_ACK = "com.wawp.windwatch.ACK";
    static final String ACTION_TEST = "com.wawp.windwatch.TEST";
    static final String ACTION_TEST_WARNING = "com.wawp.windwatch.TEST_WARNING";

    static final String PREF = "wind_watch_state";
    static final String PREF_ALARM_URI = "alarm_uri";
    static final String PREF_ALARM_NAME = "alarm_name";

    static final double PRE_ALERT_KT = 14.0;
    static final double WARNING_KT = 15.0;
    static final double FAST_POLL_KT = 12.0;
    static final long NORMAL_POLL_MS = 10_000L;
    static final long FAST_POLL_MS = 5_000L;
    // AWOSNet currently shows a normal publication delay close to 2-3 minutes.
    // Five minutes avoids the false stale/fresh flapping seen with the old 180 s limit.
    static final long STALE_AFTER_MS = 5 * 60_000L;
    static final double RESET_KT = 12.0;
    static final long RESET_HOLD_MS = 10 * 60_000L;

    private static final String CH_MONITOR = "monitor";
    // Audio is played by MediaPlayer so a user-selected local sound can be used.
    private static final String CH_PRE = "prealert_v3";
    private static final String CH_WARN = "warning_v3";
    private static final String CH_DATA = "data_problem";
    private static final int N_MONITOR = 100, N_PRE = 1400, N_WARN = 1500, N_DATA = 1600;

    private HandlerThread workerThread;
    private Handler worker;
    private PowerManager.WakeLock wakeLock;
    private final AwosClient client = new AwosClient();
    private EventLogDb db;
    private AlertState alertState = AlertState.NORMAL;
    private long belowResetSince = -1L;
    private boolean dataProblemNotified = false;
    private MediaPlayer alarmPlayer;
    private Vibrator vibrator;
    private boolean warningSoundActive = false;

    enum AlertState { NORMAL, PRE_ALERT, WARNING }

    @Override public void onCreate() {
        super.onCreate();
        db = new EventLogDb(this);
        createChannels();
        workerThread = new HandlerThread("WAWP-Wind-Watch");
        workerThread.start();
        worker = new Handler(workerThread.getLooper());
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "WAWPWindWatch:monitor");
        wakeLock.setReferenceCounted(false);
        vibrator = (Vibrator) getSystemService(VIBRATOR_SERVICE);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) { stopMonitoring(); return START_NOT_STICKY; }
        if (ACTION_ACK.equals(action)) { ack(); return START_STICKY; }
        if (ACTION_TEST.equals(action)) {
            showThresholdAlert(false, 14.0, 11.0, 14.0, 100.0, "TEST ONLY");
            playPreAlertSound();
            db.add("TEST", "Test 14 KT pre-alert sound sent");
            return START_STICKY;
        }
        if (ACTION_TEST_WARNING.equals(action)) {
            showThresholdAlert(true, 15.0, 12.0, 15.0, 100.0, "TEST ONLY");
            startWarningSound();
            db.add("TEST", "Test 15 KT repeating warning sound started");
            return START_STICKY;
        }

        startForeground(N_MONITOR, monitorNotification("Starting AWOS monitoring…"));
        if (!wakeLock.isHeld()) wakeLock.acquire();
        getSharedPreferences(PREF, MODE_PRIVATE).edit().putBoolean("monitoring", true).apply();
        db.add("SYSTEM", "Monitoring started");
        worker.removeCallbacksAndMessages(null);
        worker.post(this::pollOnce);
        return START_STICKY;
    }

    private void pollOnce() {
        long next = NORMAL_POLL_MS;
        try {
            AwosClient.Observation o = client.fetch();
            if (!o.isFresh(STALE_AFTER_MS)) {
                saveObservation(o, "DATA STALE / INVALID");
                if (!dataProblemNotified) {
                    dataProblemNotified = true;
                    showDataProblem(o);
                    db.add("DATA", "AWOS data stale/invalid; new wind alerts inhibited");
                }
            } else {
                if (dataProblemNotified) {
                    dataProblemNotified = false;
                    ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).cancel(N_DATA);
                    db.add("DATA", "Fresh AWOS data restored");
                }
                double metric = o.triggerMetric();
                evaluate(metric, o);
                next = metric >= FAST_POLL_KT ? FAST_POLL_MS : NORMAL_POLL_MS;
                saveObservation(o, alertState.name());
                updateMonitorNotification(o, next);
            }
        } catch (Exception e) {
            getSharedPreferences(PREF, MODE_PRIVATE).edit()
                    .putString("status", "OFFLINE: " + shortMessage(e))
                    .putLong("last_fetch_ms", System.currentTimeMillis()).apply();
            if (!dataProblemNotified) {
                dataProblemNotified = true;
                showDataProblem(null);
                db.add("DATA", "AWOS fetch failed: " + shortMessage(e));
            }
        } finally {
            if (worker != null) worker.postDelayed(this::pollOnce, next);
        }
    }

    private void evaluate(double metric, AwosClient.Observation o) {
        long now = System.currentTimeMillis();
        if (metric >= WARNING_KT) {
            belowResetSince = -1L;
            if (alertState != AlertState.WARNING) {
                alertState = AlertState.WARNING;
                showThresholdAlert(true, metric, o.windSpeed, o.windGust, o.windDirection, utc(o.observationEpochMs));
                startWarningSound();
                db.add("WARNING", "15 KT criterion reached; repeating alarm started; metric=" + fmt(metric) + " KT");
            }
            return;
        }
        if (metric >= PRE_ALERT_KT) {
            belowResetSince = -1L;
            if (alertState == AlertState.NORMAL) {
                alertState = AlertState.PRE_ALERT;
                showThresholdAlert(false, metric, o.windSpeed, o.windGust, o.windDirection, utc(o.observationEpochMs));
                playPreAlertSound();
                db.add("PRE", "14 KT pre-alert reached; audible alert sent; metric=" + fmt(metric) + " KT");
            }
            return;
        }
        if (alertState != AlertState.NORMAL && metric <= RESET_KT) {
            if (belowResetSince < 0L) belowResetSince = now;
            if (now - belowResetSince >= RESET_HOLD_MS) {
                AlertState previous = alertState;
                alertState = AlertState.NORMAL;
                belowResetSince = -1L;
                stopAlarmSound();
                NotificationManager nm = (NotificationManager)getSystemService(NOTIFICATION_SERVICE);
                nm.cancel(N_PRE); nm.cancel(N_WARN);
                db.add("RESET", "Alert re-armed after <=12 KT for 10 minutes (from " + previous.name() + ")");
            }
        } else if (metric > RESET_KT) {
            belowResetSince = -1L;
        }
    }

    private void saveObservation(AwosClient.Observation o, String status) {
        SharedPreferences.Editor e = getSharedPreferences(PREF, MODE_PRIVATE).edit();
        e.putString("status", status);
        e.putFloat("ws", finiteFloat(o.windSpeed));
        e.putFloat("gust", finiteFloat(o.windGust));
        e.putFloat("wd", finiteFloat(o.windDirection));
        e.putFloat("metric", finiteFloat(o.triggerMetric()));
        e.putLong("obs_ms", o.observationEpochMs);
        e.putLong("last_fetch_ms", o.fetchEpochMs);
        e.putInt("http", o.httpCode);
        e.putString("alert_state", alertState.name());
        e.apply();
    }

    private float finiteFloat(double d) { return Double.isFinite(d) ? (float)d : Float.NaN; }

    private void updateMonitorNotification(AwosClient.Observation o, long nextPoll) {
        String text = "Wind " + windText(o) + " • " + (nextPoll / 1000) + "s polling • " + alertState.name();
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(N_MONITOR, monitorNotification(text));
    }

    private Notification monitorNotification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 1, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, WindMonitorService.class).setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(this, 2, stop, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CH_MONITOR)
                .setSmallIcon(android.R.drawable.ic_menu_compass)
                .setContentTitle("WAWP Wind Watch active")
                .setContentText(text).setContentIntent(pi).setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "STOP", stopPi).build()).build();
    }

    private void showThresholdAlert(boolean warning, double metric, double ws, double gust, double wd, String observed) {
        int id = warning ? N_WARN : N_PRE;
        String channel = warning ? CH_WARN : CH_PRE;
        String title = warning ? "WAWP: 15 KT criterion reached" : "WAWP: 14 KT wind pre-alert";
        String body = "Metric " + fmt(metric) + " KT • Wind " + fmtDir(wd) + "/" + fmt(ws) + " KT" +
                (Double.isFinite(gust) ? " • Gust " + fmt(gust) + " KT" : "") + " • " + observed;
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent openPi = PendingIntent.getActivity(this, warning ? 15 : 14, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent ack = new Intent(this, WindMonitorService.class).setAction(ACTION_ACK);
        PendingIntent ackPi = PendingIntent.getService(this, warning ? 115 : 114, ack, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, channel)
                .setSmallIcon(warning ? android.R.drawable.stat_notify_error : android.R.drawable.ic_dialog_alert)
                .setContentTitle(title).setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body + "\nMonitoring only — verify AWOS and forecaster procedures before action."))
                .setContentIntent(openPi).setAutoCancel(false).setCategory(Notification.CATEGORY_ALARM)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .addAction(new Notification.Action.Builder(null, "ACKNOWLEDGE", ackPi).build()).build();
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(id, n);
    }

    private void showDataProblem(AwosClient.Observation o) {
        String body = o == null ? "Cannot fetch AWOSNet." : "No fresh valid wind observation. HTTP " + o.httpCode + ", age " + (o.ageMs()/1000) + " s.";
        Notification n = new Notification.Builder(this, CH_DATA)
                .setSmallIcon(android.R.drawable.stat_notify_error)
                .setContentTitle("WAWP AWOS monitoring problem").setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body + " New 14/15 KT alerts are inhibited until fresh data return."))
                .setCategory(Notification.CATEGORY_ERROR).build();
        ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(N_DATA, n);
    }

    private void playPreAlertSound() {
        stopAlarmSound();
        warningSoundActive = false;
        try {
            alarmPlayer = buildAlarmPlayer(false);
            if (alarmPlayer != null) {
                alarmPlayer.start();
                vibratePreAlert();
                // Pre-alert is intentionally brief; the 15 KT alarm is the repeating one.
                if (worker != null) worker.postDelayed(() -> {
                    if (!warningSoundActive) stopAlarmSound();
                }, 4_000L);
            }
        } catch (Exception e) {
            db.add("AUDIO", "Could not play 14 KT sound: " + shortMessage(e));
            stopAlarmSound();
        }
    }

    private void startWarningSound() {
        stopAlarmSound();
        warningSoundActive = true;
        try {
            alarmPlayer = buildAlarmPlayer(true);
            if (alarmPlayer != null) {
                alarmPlayer.start();
                vibrateWarning();
            }
        } catch (Exception e) {
            db.add("AUDIO", "Could not start 15 KT warning sound: " + shortMessage(e));
            stopAlarmSound();
        }
    }

    private MediaPlayer buildAlarmPlayer(boolean looping) throws Exception {
        Uri alarmUri = selectedAlarmUri();
        MediaPlayer p = new MediaPlayer();
        AudioAttributes aa = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build();
        p.setAudioAttributes(aa);
        p.setDataSource(this, alarmUri);
        p.setLooping(looping);
        p.setVolume(1.0f, 1.0f);
        try {
            p.prepare();
            return p;
        } catch (Exception customError) {
            try { p.release(); } catch (Exception ignored) { }
            // If a previously selected local file was moved/deleted, fall back to the device alarm.
            Uri fallback = defaultAlarmUri();
            MediaPlayer q = new MediaPlayer();
            q.setAudioAttributes(aa);
            q.setDataSource(this, fallback);
            q.setLooping(looping);
            q.setVolume(1.0f, 1.0f);
            q.prepare();
            db.add("AUDIO", "Selected alarm unavailable; used device default alarm");
            return q;
        }
    }

    private Uri selectedAlarmUri() {
        String saved = getSharedPreferences(PREF, MODE_PRIVATE).getString(PREF_ALARM_URI, "");
        if (saved != null && !saved.trim().isEmpty()) {
            try { return Uri.parse(saved); } catch (Exception ignored) { }
        }
        return defaultAlarmUri();
    }

    private Uri defaultAlarmUri() {
        Uri alarmUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        if (alarmUri == null) alarmUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE);
        if (alarmUri == null) alarmUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
        return alarmUri;
    }

    private void vibratePreAlert() {
        if (vibrator != null && vibrator.hasVibrator()) {
            vibrator.vibrate(VibrationEffect.createWaveform(new long[]{0, 450, 180, 450, 180, 450}, -1));
        }
    }

    private void vibrateWarning() {
        if (vibrator != null && vibrator.hasVibrator()) {
            // Repeat until ACK/STOP/reset. Index 1 repeats from the first long vibration.
            vibrator.vibrate(VibrationEffect.createWaveform(new long[]{0, 1000, 300, 1000, 300, 1000, 700}, 1));
        }
    }

    private void stopAlarmSound() {
        warningSoundActive = false;
        if (vibrator != null) vibrator.cancel();
        if (alarmPlayer != null) {
            try { if (alarmPlayer.isPlaying()) alarmPlayer.stop(); } catch (Exception ignored) { }
            try { alarmPlayer.release(); } catch (Exception ignored) { }
            alarmPlayer = null;
        }
    }

    private void ack() {
        stopAlarmSound();
        NotificationManager nm = (NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        nm.cancel(N_PRE); nm.cancel(N_WARN);
        getSharedPreferences(PREF, MODE_PRIVATE).edit().putLong("last_ack_ms", System.currentTimeMillis()).apply();
        db.add("ACK", "Current phone alert acknowledged; alarm sound stopped");
    }

    private void stopMonitoring() {
        if (worker != null) worker.removeCallbacksAndMessages(null);
        stopAlarmSound();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        getSharedPreferences(PREF, MODE_PRIVATE).edit().putBoolean("monitoring", false).putString("status", "STOPPED").apply();
        db.add("SYSTEM", "Monitoring stopped");
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private void createChannels() {
        NotificationManager nm = (NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel monitor = new NotificationChannel(CH_MONITOR, "WAWP monitoring", NotificationManager.IMPORTANCE_LOW);
        monitor.setSound(null, null);
        nm.createNotificationChannel(monitor);

        // Sound is handled directly by MediaPlayer so local files can be selected and looped.
        NotificationChannel pre = new NotificationChannel(CH_PRE, "14 KT pre-alert", NotificationManager.IMPORTANCE_HIGH);
        pre.enableVibration(false);
        pre.setSound(null, null);
        nm.createNotificationChannel(pre);

        NotificationChannel warn = new NotificationChannel(CH_WARN, "15 KT warning", NotificationManager.IMPORTANCE_HIGH);
        warn.enableVibration(false);
        warn.setSound(null, null);
        nm.createNotificationChannel(warn);

        NotificationChannel data = new NotificationChannel(CH_DATA, "AWOS source problems", NotificationManager.IMPORTANCE_HIGH);
        data.enableVibration(true);
        nm.createNotificationChannel(data);
    }

    private String windText(AwosClient.Observation o) {
        return fmtDir(o.windDirection) + "/" + fmt(o.windSpeed) + "KT" + (Double.isFinite(o.windGust) ? " G" + fmt(o.windGust) : "");
    }

    private static String fmt(double d) { return Double.isFinite(d) ? String.format(Locale.US, "%.0f", d) : "--"; }
    private static String fmtDir(double d) { return Double.isFinite(d) ? String.format(Locale.US, "%03.0f°", d) : "---°"; }
    private static String shortMessage(Throwable t) { String m = t.getMessage(); return m == null ? t.getClass().getSimpleName() : m; }

    private static String utc(long epoch) {
        if (epoch <= 0) return "time unknown";
        SimpleDateFormat f = new SimpleDateFormat("dd HH:mm:ss'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return f.format(new Date(epoch));
    }

    @Override public void onDestroy() {
        if (worker != null) worker.removeCallbacksAndMessages(null);
        if (workerThread != null) workerThread.quitSafely();
        stopAlarmSound();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
