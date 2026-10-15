#!/usr/bin/env bash
set -euo pipefail
: "${ANDROID_SDK_ROOT:?Set ANDROID_SDK_ROOT to your Android SDK directory}"
lab_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ANDROID_AVD_HOME="${RETOUCH_AVD_HOME:-$lab_dir/.retouch-avd}"
mkdir -p "$ANDROID_AVD_HOME"
if [[ -x "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/avdmanager" ]]; then
  avdmanager="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/avdmanager"
else
  avdmanager="$ANDROID_SDK_ROOT/cmdline-tools/bin/avdmanager"
fi
image='system-images;android-30;google_apis;x86_64'
if [[ ! -d "$ANDROID_AVD_HOME/retouch-lab.avd" ]]; then
  "$avdmanager" create avd --name retouch-lab --package "$image" --device pixel_2 <<< no
fi
acceleration=auto
if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then acceleration=off; fi
exec "$ANDROID_SDK_ROOT/emulator/emulator" -avd retouch-lab \
  -accel "$acceleration" -no-window -no-audio -no-snapshot -no-boot-anim \
  -gpu swiftshader -memory 2048 -cores 2 -camera-back none -camera-front none \
  -skin 480x800 -no-metrics
