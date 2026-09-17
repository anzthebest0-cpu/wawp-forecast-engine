package com.wawp.windwatch;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

final class EventLogDb extends SQLiteOpenHelper {
    private static final String DB = "wind_watch.db";
    private static final int VERSION = 1;

    EventLogDb(Context context) { super(context, DB, null, VERSION); }

    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL)");
    }
    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) { }

    void add(String level, String message) {
        ContentValues v = new ContentValues();
        v.put("ts", System.currentTimeMillis());
        v.put("level", level);
        v.put("message", message);
        getWritableDatabase().insert("events", null, v);
    }

    String recent(int limit) {
        StringBuilder b = new StringBuilder();
        try (Cursor c = getReadableDatabase().query("events", new String[]{"ts","level","message"}, null, null, null, null, "ts DESC", Integer.toString(limit))) {
            SimpleDateFormat f = new SimpleDateFormat("dd MMM HH:mm:ss", Locale.getDefault());
            while (c.moveToNext()) {
                b.append(f.format(new Date(c.getLong(0))))
                 .append("  ").append(c.getString(1))
                 .append("  ").append(c.getString(2)).append('\n');
            }
        }
        return b.length() == 0 ? "No events yet." : b.toString();
    }
}
