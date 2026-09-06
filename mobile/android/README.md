# Jarvis Android client

This is a native Java Android app for package `com.arjun.jarvis` (min SDK 26, target SDK 35). It has no web overlay and does not start a microphone after boot.

## Build prerequisites

1. Open `mobile/android` in Android Studio with Android SDK 35 installed.
2. Download the Vosk **small English** Android model separately and unpack it as `app/src/main/assets/model-en-us/`. The model is deliberately excluded from source control.
3. Build with `./gradlew assembleDebug` once the Android/Gradle tooling is available.

The wake service is started only from the in-app **Start wake** button after microphone permission. It uses Vosk locally for the constrained wake phrase `Jarvis`, then switches to unrestricted local Vosk transcription for one command. The persistent notification contains a **Stop** action.

## Server settings and API contract

Set the server URL to either a Tailscale HTTPS endpoint or a user-selected LAN URL such as `http://100.x.y.z:8770`, plus the mobile API token. The app sends `Authorization: Bearer <token>` when a token is configured.

The backend must authenticate and implement:

| Request | Body / result |
| --- | --- |
| `POST /chat` | `{"message":"...","session_id":"mobile"}` → `{"reply":"..."}` |
| `POST /mobile/control` | `{"action":"move","dx":...,"dy":...}`, `{"action":"click"}`, `{"action":"keys","keys":"ENTER"}`, or `{"action":"type","text":"..."}` |
| `GET /mobile/screen` | screenshot image bytes |

The Android manifest permits cleartext only because the user may choose a private Tailscale/LAN HTTP URL. Use HTTPS whenever it is available.
