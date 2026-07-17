# iOS build image — the macOS toolchain host.
#
# ── Why this is so much thinner than android/Dockerfile ──
# The Android image installs the whole toolchain (JDK + Android SDK) because that toolchain is
# Linux software. The iOS toolchain (Xcode / xcodebuild / codesign) is macOS-only and cannot be
# installed into a Linux image at all. So there is nothing to `apt-get install` here: the "toolchain"
# is a full macOS guest that boots inside this container via QEMU/KVM (dockurr/macos).
#
# This Dockerfile therefore exists only to PIN the base image and label the build — the real setup
# (installing Xcode) happens once inside the running VM, interactively, because it needs an Apple ID
# and the App Store. See README.md.
#
# Apple EULA: macOS on non-Apple hardware is a gray area. Personal use only, at your own risk.

FROM dockurr/macos:latest

LABEL org.opencontainers.image.title="xauorderpad-ios-buildvm"
LABEL org.opencontainers.image.description="macOS build VM for the XauOrderPad iOS app"

# Nothing else can be baked in: Xcode is installed inside the guest, not the container.
