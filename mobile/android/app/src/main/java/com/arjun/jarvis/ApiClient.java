package com.arjun.jarvis;

import android.content.Context;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/** Small authenticated HTTP client. Calls are always made off the Android main thread. */
final class ApiClient {
    private ApiClient() {}

    private static HttpURLConnection connection(Context context, String path, String method) throws Exception {
        String base = Prefs.baseUrl(context);
        if (base.isEmpty()) throw new IllegalStateException("Set the Jarvis server URL first.");
        HttpURLConnection c = (HttpURLConnection) new URL(base + path).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(10000);
        c.setReadTimeout(60000);
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        String token = Prefs.token(context);
        if (!token.isEmpty()) c.setRequestProperty("Authorization", "Bearer " + token);
        return c;
    }

    static String chat(Context context, String text) throws Exception {
        JSONObject body = new JSONObject().put("message", text).put("session_id", "mobile");
        return post(context, "/chat", body).optString("reply", "No reply returned.");
    }

    static JSONObject control(Context context, JSONObject action) throws Exception {
        return post(context, "/mobile/control", action);
    }

    static InputStream screen(Context context) throws Exception {
        HttpURLConnection c = connection(context, "/mobile/screen", "GET");
        int status = c.getResponseCode();
        if (status < 200 || status >= 300) throw new IllegalStateException("Screen request failed (HTTP " + status + ")");
        return c.getInputStream();
    }

    /** POST raw mono PCM16 @ 16 kHz to the server's Whisper endpoint; returns the transcript. */
    static String transcribe(Context context, byte[] pcm) throws Exception {
        HttpURLConnection c = connection(context, "/mobile/transcribe", "POST");
        c.setRequestProperty("Content-Type", "application/octet-stream");
        c.setFixedLengthStreamingMode(pcm.length);
        c.setDoOutput(true);
        c.getOutputStream().write(pcm);
        int status = c.getResponseCode();
        InputStream stream = status >= 200 && status < 300 ? c.getInputStream() : c.getErrorStream();
        String result = read(stream);
        if (status < 200 || status >= 300) throw new IllegalStateException("Transcribe error " + status + ": " + result);
        return new JSONObject(result.isEmpty() ? "{}" : result).optString("text", "");
    }

    static JSONObject status(Context context) throws Exception {
        HttpURLConnection c = connection(context, "/mobile/status", "GET");
        int status = c.getResponseCode();
        InputStream stream = status >= 200 && status < 300 ? c.getInputStream() : c.getErrorStream();
        String result = read(stream);
        if (status < 200 || status >= 300) throw new IllegalStateException("Status error " + status + ": " + result);
        return new JSONObject(result.isEmpty() ? "{}" : result);
    }

    private static JSONObject post(Context context, String path, JSONObject body) throws Exception {
        HttpURLConnection c = connection(context, path, "POST");
        c.setDoOutput(true);
        byte[] bytes = body.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);
        c.getOutputStream().write(bytes);
        int status = c.getResponseCode();
        InputStream stream = status >= 200 && status < 300 ? c.getInputStream() : c.getErrorStream();
        String result = read(stream);
        if (status < 200 || status >= 300) throw new IllegalStateException("Server error " + status + ": " + result);
        return new JSONObject(result.isEmpty() ? "{}" : result);
    }

    private static String read(InputStream stream) throws Exception {
        if (stream == null) return "";
        BufferedReader reader = new BufferedReader(new InputStreamReader(stream));
        StringBuilder out = new StringBuilder(); String line;
        while ((line = reader.readLine()) != null) out.append(line);
        return out.toString();
    }
}
