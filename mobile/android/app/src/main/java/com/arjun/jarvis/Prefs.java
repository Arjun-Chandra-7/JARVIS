package com.arjun.jarvis;

import android.content.Context;
import android.content.SharedPreferences;

/** Local-only settings; the token is never added to a URL or logs. */
final class Prefs {
    private static final String FILE = "jarvis_mobile";
    static SharedPreferences get(Context context) { return context.getSharedPreferences(FILE, Context.MODE_PRIVATE); }
    static String baseUrl(Context c) { return get(c).getString("base_url", ""); }
    static String token(Context c) { return get(c).getString("token", ""); }
    static void save(Context c, String url, String token) {
        get(c).edit().putString("base_url", url.trim().replaceAll("/+$", ""))
                .putString("token", token.trim()).apply();
    }
}
