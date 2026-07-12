# Android build image: JDK 17 + Android SDK + adb. No Android Studio, no emulator.
#
# The SDK (~2-3 GB, required to compile) and Android Studio (~8-10 GB IDE) are commonly
# conflated. Only the SDK is needed, and it lives entirely in here, so nothing heavy
# lands on Windows.
#
# adb is installed INSIDE the image on purpose. Wireless adb is plain TCP, so the
# container can `adb connect <phone-ip>:<port>` over the LAN and run
# `gradlew installDebug` end-to-end -- compile AND install in one step, with no hop
# through the Windows host. (A USB cable would not help anyway: Docker Desktop on
# Windows cannot pass USB through.)
#
# JDK 17 is not a legacy pin. It is what the app's `compileOptions` and
# `jvmTarget = "17"` ask for, and Gradle 9 requires 17+ regardless.
FROM eclipse-temurin:17-jdk

ENV ANDROID_HOME=/opt/android-sdk \
    DEBIAN_FRONTEND=noninteractive
ENV PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Android command-line tools, in their own layer: this almost never changes, so an SDK
# bump below does not re-download the toolchain.
#
# sdkmanager insists on living at $ANDROID_HOME/cmdline-tools/latest/ -- unzipping it
# anywhere else fails with an opaque "Could not determine SDK root".
#
# 14742923 is the current build. The previous pin here (11076708) was from 2024 and its
# sdkmanager does not know android-36 exists, so it cannot install it.
ARG CMDLINE_TOOLS_VERSION=14742923
RUN mkdir -p "$ANDROID_HOME/cmdline-tools" \
    && curl -fsSL -o /tmp/tools.zip \
        "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_VERSION}_latest.zip" \
    && unzip -q /tmp/tools.zip -d /tmp/cmdline \
    && mv /tmp/cmdline/cmdline-tools "$ANDROID_HOME/cmdline-tools/latest" \
    && rm -rf /tmp/tools.zip /tmp/cmdline

# Both SDK 35 and 36 are installed deliberately. The app currently sets compileSdk 36,
# but its AGP (8.7.3) predates SDK 36; carrying both means the image does not have to be
# rebuilt whichever way that mismatch is settled.
#
# `yes | sdkmanager --licenses` accepts the licences non-interactively. Without it the
# first build blocks forever on a prompt nobody sees.
#
# platform-tools is what puts adb in the container.
RUN yes | sdkmanager --licenses > /dev/null \
    && sdkmanager --install \
        "platform-tools" \
        "platforms;android-35" \
        "platforms;android-36" \
        "build-tools;35.0.0" \
        "build-tools;36.0.0" \
    > /dev/null

WORKDIR /workspace
