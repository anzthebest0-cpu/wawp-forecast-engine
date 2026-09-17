package com.wawp.windwatch;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

public class MainActivity extends Activity {
    private TextView status, wind, metric, age, history;
    private final Handler ui = new Handler();
    private EventLogDb db;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        db = new EventLogDb(this);
        requestNotifications();
        setContentView(buildUi());
    }

    @Override protected void onResume() { super.onResume(); ui.post(refreshLoop); }
    @Override protected void onPause() { ui.removeCallbacks(refreshLoop); super.onPause(); }

    private final Runnable refreshLoop = new Runnable() {
        @Override public void run() { refresh(); ui.postDelayed(this, 1000); }
    };

    private View buildUi() {
        ScrollView sc = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(20), dp(20), dp(30));
        sc.addView(root);

        root.addView(tv("WAWP WIND WATCH", 26, true));
        TextView note = tv("Prototype • monitoring/advisory only • does not issue an AD WARNING", 13, false);
        note.setTextColor(Color.DKGRAY); root.addView(note);

        status = card("MONITORING: --", 22); root.addView(status);
        wind = card("WIND: --", 22); root.addView(wind);
        metric = card("ALERT METRIC: -- / 15 KT", 22); root.addView(metric);
        age = card("AWOS DATA: --", 16); root.addView(age);

        LinearLayout buttons = new LinearLayout(this); buttons.setOrientation(LinearLayout.VERTICAL);
        Button start = button("START MONITORING"); Button stop = button("STOP MONITORING");
        Button test = button("TEST 14 KT PRE-ALERT SOUND");
        Button testWarning = button("TEST 15 KT WARNING SOUND");
        Button ack = button("ACKNOWLEDGE ALERT / STOP SOUND");
        buttons.addView(start); buttons.addView(stop); buttons.addView(test); buttons.addView(testWarning); buttons.addView(ack); root.addView(buttons);

        TextView h = tv("Recent events", 18, true); h.setPadding(0, dp(18), 0, dp(6)); root.addView(h);
        history = tv("", 13, false); history.setBackgroundColor(Color.rgb(245,245,245)); history.setPadding(dp(12),dp(12),dp(12),dp(12)); root.addView(history);

        start.setOnClickListener(v -> action(WindMonitorService.ACTION_START));
        stop.setOnClickListener(v -> action(WindMonitorService.ACTION_STOP));
        test.setOnClickListener(v -> action(WindMonitorService.ACTION_TEST));
        testWarning.setOnClickListener(v -> action(WindMonitorService.ACTION_TEST_WARNING));
        ack.setOnClickListener(v -> action(WindMonitorService.ACTION_ACK));
        return sc;
    }

    private void action(String a) {
        Intent i = new Intent(this, WindMonitorService.class).setAction(a);
        if (WindMonitorService.ACTION_START.equals(a)) startForegroundService(i); else startService(i);
    }

    private void refresh() {
        SharedPreferences p = getSharedPreferences(WindMonitorService.PREF, MODE_PRIVATE);
        boolean monitoring = p.getBoolean("monitoring", false);
        String s = p.getString("status", "STOPPED"); String alert = p.getString("alert_state", "NORMAL");
        float ws = p.getFloat("ws", Float.NaN), gust = p.getFloat("gust", Float.NaN), wd = p.getFloat("wd", Float.NaN), m = p.getFloat("metric", Float.NaN);
        long obs = p.getLong("obs_ms", -1L), fetch = p.getLong("last_fetch_ms", -1L);

        status.setText((monitoring ? "● ACTIVE" : "○ STOPPED") + "\n" + alert + " • " + s);
        if ("WARNING".equals(alert)) status.setBackgroundColor(Color.rgb(255,220,220));
        else if ("PRE_ALERT".equals(alert)) status.setBackgroundColor(Color.rgb(255,239,190));
        else status.setBackgroundColor(Color.rgb(225,245,230));

        wind.setText("WIND\n" + dir(wd) + " / " + val(ws) + " KT" + (Float.isFinite(gust) ? "\nGUST " + val(gust) + " KT" : "\nGUST --"));
        metric.setText("ALERT METRIC\n" + val(m) + " / 15 KT\n14 KT = pre-alert");
        long now = System.currentTimeMillis();
        String ageText = obs > 0 ? ((now - obs)/1000) + " s old" : "unknown timestamp";
        age.setText("AWOS DATA\nObservation: " + utc(obs) + "\nAge: " + ageText + "\nLast fetch: " + local(fetch) + "\nPolling: " + (Float.isFinite(m) && m >= 12 ? "5 s" : "10 s"));
        history.setText(db.recent(20));
    }

    private void requestNotifications() {
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 10);
        }
    }

    private TextView tv(String text, int sp, boolean bold) {
        TextView t = new TextView(this); t.setText(text); t.setTextSize(sp); t.setTextColor(Color.BLACK);
        if (bold) t.setTypeface(null, android.graphics.Typeface.BOLD); return t;
    }
    private TextView card(String text, int sp) {
        TextView t = tv(text, sp, true); t.setPadding(dp(16), dp(14), dp(16), dp(14)); t.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.setMargins(0, dp(12), 0, 0); t.setLayoutParams(lp);
        t.setBackgroundColor(Color.rgb(245,245,245)); return t;
    }
    private Button button(String text) {
        Button b = new Button(this); b.setText(text); LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.setMargins(0, dp(8), 0, 0); b.setLayoutParams(lp); return b;
    }
    private int dp(int v) { return (int)(v * getResources().getDisplayMetrics().density + 0.5f); }
    private static String val(float f) { return Float.isFinite(f) ? String.format(Locale.US,"%.0f",f) : "--"; }
    private static String dir(float f) { return Float.isFinite(f) ? String.format(Locale.US,"%03.0f°",f) : "---°"; }
    private static String utc(long e) { if(e<=0)return "--"; SimpleDateFormat f=new SimpleDateFormat("dd HH:mm:ss'Z'",Locale.US); f.setTimeZone(TimeZone.getTimeZone("UTC")); return f.format(new Date(e)); }
    private static String local(long e) { if(e<=0)return "--"; return new SimpleDateFormat("HH:mm:ss",Locale.getDefault()).format(new Date(e)); }
}
