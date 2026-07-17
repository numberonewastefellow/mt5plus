#!/usr/bin/env bash
# Guest-side build — runs INSIDE the macOS VM. It is the iOS equivalent of the
# `gradlew :app:assembleDebug` line in android/deploy.bat: it turns source into an installable
# artifact and drops it where the host can see it (the 9p /shared bridge -> Windows).
#
# Run it from the VM:  bash /shared/build.sh
# (or over SSH from build.bat, if you enabled Remote Login — see README.md)
#
# Host handoff: build.bat has already synced the source into /shared/src. This writes the unsigned
# .ipa to /shared/out, which is the Windows folder ios\build-vm\shared\out\. build.bat then copies it
# next to the APK at E:\temp\mt5_data\XauOrderPad.ipa.
set -euo pipefail

SHARED="/shared"
SRC="$SHARED/src"
OUT="$SHARED/out"
WORK="$HOME/xau-build"          # build on local APFS, NOT on 9p (9p is slow + has odd perms for Xcode)
SCHEME="XauOrderPad"

echo "[build] checking the shared mount..."
if [ ! -d "$SRC" ]; then
  # 9p share not mounted yet — mount it (needs sudo once per boot).
  echo "[build] /shared not populated; mounting 9p share 'shared'..."
  sudo mount_9p shared || true
fi
if [ ! -d "$SRC" ]; then
  echo "[build] ERROR: $SRC not found. Run build.bat sync on Windows, and make sure the 9p share is"
  echo "[build]        mounted:  sudo mount_9p shared"
  exit 1
fi

echo "[build] tools:"
xcodebuild -version || { echo "[build] ERROR: Xcode not installed. Install it from the App Store first."; exit 1; }
command -v xcodegen >/dev/null 2>&1 || { echo "[build] ERROR: xcodegen missing. Run: brew install xcodegen"; exit 1; }

echo "[build] copying source to local disk ($WORK)..."
rm -rf "$WORK"
mkdir -p "$WORK"
cp -R "$SRC/." "$WORK/"
cd "$WORK"

echo "[build] generating the Xcode project..."
xcodegen generate

echo "[build] archiving (unsigned)..."
rm -rf build
xcodebuild \
  -scheme "$SCHEME" \
  -configuration Release \
  -archivePath "build/$SCHEME.xcarchive" \
  -destination 'generic/platform=iOS' \
  archive \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO

echo "[build] exporting unsigned .ipa..."
mkdir -p "$OUT"
xcodebuild -exportArchive \
  -archivePath "build/$SCHEME.xcarchive" \
  -exportOptionsPlist "$SRC/../ExportOptions-unsigned.plist" \
  -exportPath "$OUT" 2>/dev/null || {
    # -exportArchive can be finicky with an unsigned archive on some Xcode versions. Fall back to
    # packaging the .app into a Payload/ .ipa by hand — Sideloadly signs it on Windows anyway.
    echo "[build] exportArchive fell back to manual packaging..."
    APP=$(find "build/$SCHEME.xcarchive/Products/Applications" -maxdepth 1 -name '*.app' | head -n1)
    [ -n "$APP" ] || { echo "[build] ERROR: no .app in the archive"; exit 1; }
    rm -rf /tmp/xau-payload && mkdir -p /tmp/xau-payload/Payload
    cp -R "$APP" /tmp/xau-payload/Payload/
    ( cd /tmp/xau-payload && /usr/bin/zip -qry "$OUT/$SCHEME.ipa" Payload )
  }

# Normalize the name so build.bat always finds the same file.
FOUND=$(find "$OUT" -maxdepth 1 -name '*.ipa' | head -n1)
if [ -n "$FOUND" ] && [ "$FOUND" != "$OUT/$SCHEME.ipa" ]; then
  mv -f "$FOUND" "$OUT/$SCHEME.ipa"
fi

echo "[build] DONE -> $OUT/$SCHEME.ipa"
ls -lh "$OUT/$SCHEME.ipa"
