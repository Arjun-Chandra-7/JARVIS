package com.arjun.jarvis;

import android.Manifest;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Build;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.view.MotionEvent;
import android.view.View;
import android.widget.*;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import org.json.JSONObject;
import java.util.ArrayList;

/** Text-first control surface. Android's recognizer is only a user-tapped dictation feature. */
public final class MainActivity extends AppCompatActivity {
    private TextView transcript;
    private EditText input;
    private EditText remoteText;
    private ImageView screen;
    private String lastReply = "";
    private float lastX, lastY;
    private final ActivityResultLauncher<String> micPermission = registerForActivityResult(
            new ActivityResultContracts.RequestPermission(), granted -> { if (granted) startWake(); });
    private final ActivityResultLauncher<String> notificationPermission = registerForActivityResult(
            new ActivityResultContracts.RequestPermission(), ignored -> { });

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(20, 20, 20, 20);
        LinearLayout bar = new LinearLayout(this);
        Button settings = button("Settings", v -> settings());
        Button wake = button("Start wake", v -> requestWake());
        Button dictation = button("Dictate", v -> dictate());
        bar.addView(settings); bar.addView(wake); bar.addView(dictation); root.addView(bar);
        transcript = new TextView(this); transcript.setText("Jarvis mobile ready. Configure your server URL and token.");
        ScrollView scroll = new ScrollView(this); scroll.addView(transcript); root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        LinearLayout compose = new LinearLayout(this); input = new EditText(this); input.setHint("Message Jarvis");
        compose.addView(input, new LinearLayout.LayoutParams(0, -2, 1)); compose.addView(button("Send", v -> sendChat(input.getText().toString()))); root.addView(compose);
        LinearLayout controls = new LinearLayout(this);
        controls.addView(button("Paste → PC", v -> pasteToComputer())); controls.addView(button("Copy reply", v -> copy(lastReply)));
        controls.addView(button("Screenshot", v -> loadScreen())); controls.addView(button("Click", v -> control("click")));
        root.addView(controls);
        LinearLayout remoteCompose = new LinearLayout(this); remoteText = new EditText(this); remoteText.setHint("Type on computer");
        remoteCompose.addView(remoteText, new LinearLayout.LayoutParams(0, -2, 1)); remoteCompose.addView(button("Type → PC", v -> typeToComputer(remoteText.getText().toString()))); root.addView(remoteCompose);
        screen = new ImageView(this); screen.setAdjustViewBounds(true); screen.setOnTouchListener(this::trackpad); root.addView(screen, new LinearLayout.LayoutParams(-1, 300));
        LinearLayout keys = new LinearLayout(this); keys.addView(button("←", v -> key("LEFT"))); keys.addView(button("Enter", v -> key("ENTER"))); keys.addView(button("Esc", v -> key("ESC"))); root.addView(keys);
        setContentView(root);
    }

    private Button button(String text, View.OnClickListener listener) { Button b = new Button(this); b.setText(text); b.setOnClickListener(listener); return b; }
    private void requestWake() { if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startWake(); else micPermission.launch(Manifest.permission.RECORD_AUDIO); }
    private void startWake() {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS);
        ContextCompat.startForegroundService(this, new Intent(this, WakeService.class)); append("Local wake-word service started.");
    }
    private void settings() {
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(LinearLayout.VERTICAL); layout.setPadding(50, 20, 50, 0);
        EditText url = new EditText(this); url.setHint("https://your-tailnet.ts.net or http://LAN-IP:8770"); url.setText(Prefs.baseUrl(this));
        EditText token = new EditText(this); token.setHint("Mobile API token"); token.setText(Prefs.token(this));
        layout.addView(url); layout.addView(token);
        new AlertDialog.Builder(this).setTitle("Jarvis server").setView(layout).setPositiveButton("Save", (d, w) -> Prefs.save(this, url.getText().toString(), token.getText().toString())).setNegativeButton("Cancel", null).show();
    }
    private void sendChat(String text) { if (text.trim().isEmpty()) return; append("You: " + text); input.setText(""); new Thread(() -> { try { String reply = ApiClient.chat(this, text); runOnUiThread(() -> { lastReply = reply; append("Jarvis: " + reply); }); } catch (Exception e) { runOnUiThread(() -> append("Error: " + e.getMessage())); } }).start(); }
    private void dictate() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) { append("System dictation is unavailable."); return; }
        SpeechRecognizer recognizer = SpeechRecognizer.createSpeechRecognizer(this); recognizer.setRecognitionListener(new RecognitionListener() {
            public void onReadyForSpeech(Bundle b) {} public void onBeginningOfSpeech() {} public void onRmsChanged(float r) {} public void onBufferReceived(byte[] b) {} public void onEndOfSpeech() {}
            public void onError(int error) { recognizer.destroy(); }
            public void onResults(Bundle b) { ArrayList<String> results = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION); if (results != null && !results.isEmpty()) input.setText(results.get(0)); recognizer.destroy(); }
            public void onPartialResults(Bundle b) {} public void onEvent(int t, Bundle b) {}
        });
        recognizer.startListening(new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM));
    }
    private boolean trackpad(View v, MotionEvent e) { if (e.getAction() == MotionEvent.ACTION_DOWN) { lastX=e.getX(); lastY=e.getY(); return true; } if (e.getAction() == MotionEvent.ACTION_MOVE) { float dx=e.getX()-lastX, dy=e.getY()-lastY; lastX=e.getX(); lastY=e.getY(); new Thread(() -> { try { ApiClient.control(this, new JSONObject().put("action","move").put("dx",dx).put("dy",dy)); } catch (Exception ignored) {} }).start(); return true; } return true; }
    private void control(String action) { new Thread(() -> { try { ApiClient.control(this, new JSONObject().put("action", action)); } catch (Exception e) { runOnUiThread(() -> append("Control error: " + e.getMessage())); } }).start(); }
    private void key(String key) { new Thread(() -> { try { ApiClient.control(this, new JSONObject().put("action", "keys").put("keys", key)); } catch (Exception e) { runOnUiThread(() -> append("Control error: " + e.getMessage())); } }).start(); }
    private void pasteToComputer() { ClipboardManager c = getSystemService(ClipboardManager.class); if (c.hasPrimaryClip()) typeToComputer(c.getPrimaryClip().getItemAt(0).coerceToText(this).toString()); }
    private void typeToComputer(String text) { if (text.trim().isEmpty()) return; new Thread(() -> { try { ApiClient.control(this, new JSONObject().put("action", "type").put("text", text)); runOnUiThread(() -> remoteText.setText("")); } catch (Exception e) { runOnUiThread(() -> append("Type error: " + e.getMessage())); } }).start(); }
    private void loadScreen() { new Thread(() -> { try (java.io.InputStream in = ApiClient.screen(this)) { Bitmap image = BitmapFactory.decodeStream(in); runOnUiThread(() -> screen.setImageBitmap(image)); } catch (Exception e) { runOnUiThread(() -> append("Screen error: " + e.getMessage())); } }).start(); }
    private void copy(String text) { getSystemService(ClipboardManager.class).setPrimaryClip(ClipData.newPlainText("Jarvis reply", text)); }
    private void append(String line) { transcript.append("\n\n" + line); }
}
