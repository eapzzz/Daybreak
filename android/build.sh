#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
sdk=${ANDROID_SDK_ROOT:-${ANDROID_HOME:?Set ANDROID_SDK_ROOT to the Android SDK directory}}
tools="$sdk/build-tools/${DAYBREAK_BUILD_TOOLS:-36.0.0}"
api="$sdk/platforms/android-34/android.jar"
output=${1:?Pass a path for the unsigned APK}
work_dir=$(mktemp -d)
trap 'rm -r -- "$work_dir"' EXIT
mkdir "$work_dir/classes" "$work_dir/dex"

"$tools/aapt2" link -I "$api" --manifest "$source_dir/AndroidManifest.xml" -o "$work_dir/resources.apk"
javac --release 8 -classpath "$api" -d "$work_dir/classes" "$source_dir"/src/daybreak/android/*.java
jar cf "$work_dir/classes.jar" -C "$work_dir/classes" .
"$tools/d8" --lib "$api" --min-api 26 --output "$work_dir/dex" "$work_dir/classes.jar"
(
    cd "$work_dir/dex"
    zip -q "$work_dir/resources.apk" classes.dex
)
"$tools/zipalign" -f 4 "$work_dir/resources.apk" "$output"
