package com.wawp.windwatch;

import android.util.Xml;
import org.xmlpull.v1.XmlPullParser;
import java.io.BufferedInputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

final class AwosClient {
    static final String ENDPOINT = "http://wawp.awosnet.com/awiAwosNet.php";

    static final class Observation {
        String airport = "";
        String date = "";
        String time = "";
        double windSpeed = Double.NaN;
        double windDirection = Double.NaN;
        double windGust = Double.NaN;
        long observationEpochMs = -1L;
        long fetchEpochMs = System.currentTimeMillis();
        int httpCode = -1;

        double triggerMetric() {
            double v = Double.NaN;
            if (Double.isFinite(windSpeed)) v = windSpeed;
            if (Double.isFinite(windGust)) v = Double.isFinite(v) ? Math.max(v, windGust) : windGust;
            return v;
        }

        long ageMs() {
            if (observationEpochMs <= 0L) return Long.MAX_VALUE;
            return Math.max(0L, fetchEpochMs - observationEpochMs);
        }

        boolean isFresh(long staleAfterMs) {
            return httpCode == 200 && Double.isFinite(triggerMetric()) && observationEpochMs > 0L && ageMs() <= staleAfterMs;
        }
    }

    Observation fetch() throws Exception {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(ENDPOINT + "?t=" + System.currentTimeMillis());
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);
            conn.setUseCaches(false);
            conn.setRequestProperty("Cache-Control", "no-cache");
            conn.setRequestProperty("User-Agent", "WAWP-Wind-Watch/0.2");

            Observation o = new Observation();
            o.httpCode = conn.getResponseCode();
            o.fetchEpochMs = System.currentTimeMillis();
            if (o.httpCode != 200) return o;

            XmlPullParser parser = Xml.newPullParser();
            parser.setInput(new BufferedInputStream(conn.getInputStream()), "UTF-8");
            int event = parser.getEventType();
            while (event != XmlPullParser.END_DOCUMENT) {
                if (event == XmlPullParser.START_TAG) {
                    String name = parser.getName();
                    if (name != null) {
                        String key = name.trim().toLowerCase(Locale.US);
                        if (key.equals("airportidentifier") || key.equals("date") || key.equals("time") ||
                                key.equals("twominutewindspeed") || key.equals("twominutewinddirection") || key.equals("windgust")) {
                            // AWOSNet exposes measurements as value="..." attributes on self-closing tags.
                            // Example: <twoMinuteWindSpeed value="5" units="knots" ... />
                            String raw = parser.getAttributeValue(null, "value");
                            apply(o, key, raw);
                        }
                    }
                }
                event = parser.next();
            }
            o.observationEpochMs = parseUtc(o.date, o.time);
            return o;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static void apply(Observation o, String key, String raw) {
        String text = raw == null ? "" : raw.trim();
        switch (key) {
            case "airportidentifier": o.airport = text; break;
            case "date": o.date = text; break;
            case "time": o.time = text; break;
            case "twominutewindspeed": o.windSpeed = number(text); break;
            case "twominutewinddirection": o.windDirection = number(text); break;
            case "windgust": o.windGust = number(text); break;
        }
    }

    private static double number(String s) {
        try {
            if (s == null || s.trim().isEmpty() || s.contains("/") || s.contains("#") || s.contains("*")) return Double.NaN;
            String cleaned = s.replaceAll("[^0-9.+-]", "");
            if (cleaned.isEmpty()) return Double.NaN;
            return Double.parseDouble(cleaned);
        } catch (Exception e) {
            return Double.NaN;
        }
    }

    private static long parseUtc(String date, String time) {
        String[] patterns = {"dd/MM/yyyy HH:mm:ss", "dd/MM/yyyy HH:mm", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm"};
        String value = ((date == null ? "" : date.trim()) + " " + (time == null ? "" : time.trim())).trim();
        for (String p : patterns) {
            SimpleDateFormat f = new SimpleDateFormat(p, Locale.US);
            f.setLenient(false);
            f.setTimeZone(TimeZone.getTimeZone("UTC"));
            try {
                Date d = f.parse(value);
                if (d != null) return d.getTime();
            } catch (ParseException ignored) { }
        }
        return -1L;
    }
}
