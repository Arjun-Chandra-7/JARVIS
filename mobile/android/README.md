# Jarvis Android client

This is a native Java Android app for package `com.arjun.jarvis` (min SDK 26, target SDK 35). It has no web overlay and does not start a microphone after boot.

## Build

Run `scripts/build-android.sh` from the repo root. It fetches a pinned JDK 17, Gradle 8.7,
the Android cmdline-tools + `platforms;android-35` + `build-tools;35.0.0`, and the Vosk
**small English** model into `mobile/android/.tooling/` (all git-excluded), then runs
`gradle assembleDebug`. Output: `app/build/outputs/apk/debug/app-debug.apk`.

Install on the phone: `adb install -r app/build/outputs/apk/debug/app-debug.apk`.

Or open `mobile/android` in Android Studio (SDK 35) and build normally.

The wake service is started only from the in-app **Start wake** button after microphone permission. It uses Vosk locally for the constrained wake phrase `Jarvis`, then switches to unrestricted local Vosk transcription for one command. The persistent notification contains a **Stop** action.

## Pairing and connection

1. On the PC: `python scripts/pair-mobile.py` (prints/creates `~/.config/jarvis/mobile-token`,
   mode 600). `--show` prints it again; `--rotate` replaces it.
2. Expose the backend over your tailnet: `tailscale serve --bg 8770`.
3. In the app **Settings**: server URL = `https://<machine>.<tailnet>.ts.net`, token = the value
   from step 1. On home Wi-Fi a LAN URL like `http://192.168.x.y:8770` also works.

The app sends `Authorization: Bearer <token>` on every request. The status bar polls
`GET /mobile/status` and shows online/offline, brain, away mode, Meet state, and the newest
coding job.

## Backend API contract (implemented in `jarvis/mobile.py`, `jarvis/webserver.py`)

| Request | Body / result |
| --- | --- |
| `POST /chat` | `{"message":"...","session_id":"mobile"}` → `{"reply":"..."}` |
| `POST /mobile/control` | `{"action":"move","dx":..,"dy":..}` (relative) or `{"action":"move","x":..,"y":..}`, `{"action":"click","button":"left"}`, `{"action":"keys","keys":"ctrl+t"}`, `{"action":"type","text":".."}`, `{"action":"scroll","direction":"down"}`, `copy`, `paste` → `{"ok":true}` |
| `GET /mobile/screen` | PNG bytes + `X-Screen-Width/Height` |
| `POST /mobile/transcribe` | raw mono PCM16 @ 16 kHz body → `{"text":".."}` |
| `GET /mobile/status` | `{ok, brain, online, away, meet, coding_jobs[], cpu, mem}` |

Every `/mobile/*` route needs the token — there is no localhost exemption and an unset token
denies all requests. Repeated bad tokens from one host are rate-limited (429). Cleartext is
allowed in the manifest only for a private LAN/Tailscale HTTP URL; prefer the HTTPS tailnet name.
