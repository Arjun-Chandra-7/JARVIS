package com.arjun.jarvis;

import android.app.*;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.*;
import android.speech.tts.*;
import androidx.core.app.NotificationCompat;
import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;
import org.vosk.android.*;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Locale;

/** User-started, non-sticky foreground microphone service; never runs after boot. */
public final class WakeService extends Service implements RecognitionListener {
    static final String ACTION_STOP = "com.arjun.jarvis.STOP_WAKE";
    private static final String CHANNEL = "jarvis_wake";
    private static final int RATE = 16000;
    private Model model;
    private Recognizer recognizer;
    private SpeechService speech;
    private TextToSpeech tts;
    private final Handler handler = new Handler();
    private volatile boolean active, destroyed, capturing, speaking;

    @Override public void onCreate() {
        super.onCreate(); createChannel();
        tts = new TextToSpeech(this, ignored -> tts.setLanguage(Locale.US));
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override public void onStart(String id) { }
            @Override public void onDone(String id) { resumeAfterSpeech(); }
            @Override public void onError(String id) { resumeAfterSpeech(); }
        });
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) { stopListening(); stopSelf(); return START_NOT_STICKY; }
        active = true; // repeated Start requests are idempotent
        Notification initial = notification("Loading local wake model…");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) startForeground(7, initial, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        else startForeground(7, initial);
        loadModel();
        return START_NOT_STICKY;
    }

    private void loadModel() {
        if (model != null) { startWakeRecognition(); return; }
        StorageService.unpack(this, "model-en-us", "model-en-us", loaded -> {
            if (destroyed || !active) { loaded.close(); return; }
            model = loaded; startWakeRecognition();
        }, error -> { if (!destroyed && active) updateNotification("Wake model missing: " + error.getMessage()); });
    }

    private void startWakeRecognition() {
        if (destroyed || !active || speaking || capturing || model == null) return;
        closeRecognition();
        try {
            // [unk] prevents non-wake speech from being falsely interpreted as the wake word.
            recognizer = new Recognizer(model, RATE, "[\"jarvis\", \"[unk]\"]");
            speech = new SpeechService(recognizer, RATE);
            speech.startListening(this);
            updateNotification("Listening locally for “Jarvis”");
        } catch (IOException error) { updateNotification("Microphone error: " + error.getMessage()); }
    }

    /** Stop, shut down AudioRecord, and close native Vosk resources before any microphone handoff. */
    private void closeRecognition() {
        SpeechService oldSpeech = speech; speech = null;
        Recognizer oldRecognizer = recognizer; recognizer = null;
        if (oldSpeech != null) { try { oldSpeech.stop(); } catch (Exception ignored) { } try { oldSpeech.shutdown(); } catch (Exception ignored) { } }
        if (oldRecognizer != null) try { oldRecognizer.close(); } catch (Exception ignored) { }
    }

    @Override public void onPartialResult(String hypothesis) { handleWake(hypothesis); }
    @Override public void onResult(String hypothesis) { handleWake(hypothesis); }
    @Override public void onFinalResult(String hypothesis) { handleWake(hypothesis); }
    @Override public void onError(Exception error) { if (active && !destroyed) updateNotification("Wake error: " + error.getMessage()); }
    @Override public void onTimeout() { startWakeRecognition(); }

    private void handleWake(String json) {
        if (capturing || speaking || !active) return;
        try {
            JSONObject result = new JSONObject(json);
            String text = result.optString("partial", result.optString("text", "")).toLowerCase(Locale.US);
            if (text.matches(".*\\bjarvis\\b.*")) captureCommand(); // partial result avoids waiting for end-of-utterance
        } catch (Exception ignored) { }
    }

    /** Capture raw PCM and let the server's Whisper model transcribe the command. */
    private void captureCommand() {
        if (capturing || destroyed || !active) return;
        capturing = true; closeRecognition(); updateNotification("Listening for a command…");
        new Thread(() -> {
            byte[] pcm = recordCommandPcm();
            if (destroyed || !active) return;
            if (pcm.length == 0) { capturing = false; startWakeRecognition(); return; }
            try {
                String command = ApiClient.transcribe(this, pcm);
                if (command.trim().isEmpty()) { capturing = false; startWakeRecognition(); return; }
                String reply = ApiClient.chat(this, command);
                capturing = false; speakAndResume(reply);
            } catch (Exception error) {
                capturing = false; speakAndResume("Jarvis server error. " + error.getMessage());
            }
        }, "jarvis-command-capture").start();
    }

    /** Up to 9 seconds of mono PCM16, ending after speech plus 900 ms silence. */
    private byte[] recordCommandPcm() {
        int minimum = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (minimum <= 0) return new byte[0];
        AudioRecord recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minimum * 2);
        byte[] buffer = new byte[minimum]; ByteArrayOutputStream output = new ByteArrayOutputStream();
        boolean heard = false; long silence = 0, deadline = System.currentTimeMillis() + 9000;
        try {
            recorder.startRecording();
            while (active && !destroyed && System.currentTimeMillis() < deadline) {
                int count = recorder.read(buffer, 0, buffer.length); if (count <= 0) continue;
                output.write(buffer, 0, count); long energy = 0;
                for (int i = 0; i + 1 < count; i += 2) { short s = (short) ((buffer[i] & 0xff) | (buffer[i + 1] << 8)); energy += Math.abs((int) s); }
                boolean voiced = energy / Math.max(1, count / 2) > 450;
                if (voiced) { heard = true; silence = 0; }
                else if (heard) { if (silence == 0) silence = System.currentTimeMillis(); if (System.currentTimeMillis() - silence >= 900) break; }
            }
        } catch (Exception ignored) { return new byte[0];
        } finally { try { recorder.stop(); } catch (Exception ignored) { } recorder.release(); }
        return heard ? output.toByteArray() : new byte[0];
    }

    private void speakAndResume(String text) {
        speaking = true; // prevent recognizer restart/self-wake before TTS is scheduled
        handler.post(() -> { if (!destroyed && active && tts != null) tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis-reply"); });
    }
    private void resumeAfterSpeech() { handler.post(() -> { speaking = false; startWakeRecognition(); }); }

    private void stopListening() { active = false; capturing = false; handler.removeCallbacksAndMessages(null); closeRecognition(); }
    private void createChannel() { getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel(CHANNEL, "Jarvis wake word", NotificationManager.IMPORTANCE_LOW)); }
    private Notification notification(String text) {
        Intent stop = new Intent(this, StopWakeReceiver.class).setAction(ACTION_STOP);
        PendingIntent pending = PendingIntent.getBroadcast(this, 1, stop, PendingIntent.FLAG_IMMUTABLE);
        return new NotificationCompat.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("Jarvis wake word active").setContentText(text).setOngoing(true).addAction(new NotificationCompat.Action(0, "Stop", pending)).build();
    }
    private void updateNotification(String text) { if (!destroyed) getSystemService(NotificationManager.class).notify(7, notification(text)); }
    @Override public void onDestroy() { destroyed = true; stopListening(); if (model != null) { model.close(); model = null; } if (tts != null) tts.shutdown(); super.onDestroy(); }
    @Override public IBinder onBind(Intent intent) { return null; }
}
