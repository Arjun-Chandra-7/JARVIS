#!/usr/bin/env bash
# Reproducible local Android build. Tooling, SDK, model, caches, and APK remain gitignored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANDROID_DIR="$ROOT/mobile/android"
TOOLING="$ANDROID_DIR/.tooling"
SDK="$TOOLING/android-sdk"
JDK="$TOOLING/jdk-17"
GRADLE="$TOOLING/gradle-8.7"
MODEL_DIR="$ANDROID_DIR/app/src/main/assets/model-en-us"
SDK_ZIP="$TOOLING/commandlinetools-linux-15859902_latest.zip"
MODEL_ZIP="$TOOLING/vosk-model-small-en-us-0.15.zip"

mkdir -p "$TOOLING" "$SDK/cmdline-tools"
download() { [ -f "$2" ] || curl -fL --retry 3 --retry-delay 2 "$1" -o "$2"; }

if [ ! -x "$JDK/bin/java" ]; then
  download "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse" "$TOOLING/jdk17.tar.gz"
  rm -rf "$JDK"
  mkdir -p "$JDK"
  tar -xzf "$TOOLING/jdk17.tar.gz" -C "$JDK" --strip-components=1
fi
if [ ! -x "$GRADLE/bin/gradle" ]; then
  download "https://services.gradle.org/distributions/gradle-8.7-bin.zip" "$TOOLING/gradle-8.7-bin.zip"
  unzip -q -o "$TOOLING/gradle-8.7-bin.zip" -d "$TOOLING"
fi
if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
  download "https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip" "$SDK_ZIP"
  rm -rf "$SDK/cmdline-tools/latest"
  mkdir -p "$SDK/cmdline-tools/latest"
  unzip -q -o "$SDK_ZIP" -d "$SDK/cmdline-tools/latest"
  # Google's archive has a top-level cmdline-tools/ directory; sdkmanager requires
  # the contents directly below cmdline-tools/latest/.
  if [ -x "$SDK/cmdline-tools/latest/cmdline-tools/bin/sdkmanager" ]; then
    mv "$SDK/cmdline-tools/latest/cmdline-tools"/* "$SDK/cmdline-tools/latest/"
    rmdir "$SDK/cmdline-tools/latest/cmdline-tools"
  fi
fi

export ANDROID_HOME="$SDK" ANDROID_SDK_ROOT="$SDK" JAVA_HOME="$JDK"
# sdkmanager exits successfully once it closes stdin; `yes` then gets SIGPIPE, which must not
# trip this script's `pipefail` setting.
set +o pipefail
yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null
set -o pipefail
"$SDK/cmdline-tools/latest/bin/sdkmanager" "platform-tools" "platforms;android-35" "build-tools;35.0.0"

if [ ! -f "$MODEL_DIR/uuid" ]; then
  download "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" "$MODEL_ZIP"
  rm -rf "$MODEL_DIR"
  mkdir -p "$MODEL_DIR"
  unzip -q -o "$MODEL_ZIP" -d "$TOOLING/vosk-unpack"
  source_dir="$(find "$TOOLING/vosk-unpack" -maxdepth 1 -type d -name 'vosk-model-small-en-us-0.15*' | head -n 1)"
  [ -n "$source_dir" ] && cp -a "$source_dir"/. "$MODEL_DIR"/
  [ -f "$MODEL_DIR/uuid" ] || { echo "Vosk model unpack did not provide uuid" >&2; exit 1; }
fi

cd "$ANDROID_DIR"
"$GRADLE/bin/gradle" --no-daemon --stacktrace assembleDebug
echo "APK: $ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"
