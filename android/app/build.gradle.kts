// Imported explicitly: inside a Gradle Kotlin script `java` resolves to the JavaPluginExtension,
// not the java.* package, so `java.util.Properties` does not compile.
import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Release signing comes from the ENVIRONMENT, supplied by the build harness
// (android/.env -> docker compose -> these vars). Never from a file beside the source:
// the keystore lives at /out/keystore/xau-release.jks -- on the host at E:\temp\mt5_data,
// deliberately OUTSIDE this git repo. `mt5plus` already gitignores *.pem for exactly
// this reason.
//
// If the vars are absent the release signingConfig is NOT registered, so assembleRelease
// fails loudly. That is deliberate: a "release" that silently fell back to the debug key
// would be worse than a build failure, because Android refuses to update an installed APK
// signed with a different key -- recovering means uninstalling, which loses the stored token.
val ksPath = System.getenv("XAU_KEYSTORE")
val ksPass = System.getenv("XAU_KEYSTORE_PASSWORD")
val ksAlias = System.getenv("XAU_KEY_ALIAS")
val keyPass = System.getenv("XAU_KEY_PASSWORD")
val hasSigning = !ksPath.isNullOrBlank() && !ksPass.isNullOrBlank() &&
        !ksAlias.isNullOrBlank() && !keyPass.isNullOrBlank()

// ---- dev convenience: pre-fill the Connect screen ---------------------------------------
//
// Read from android/local.properties (ALREADY gitignored, so the token cannot be committed),
// falling back to env vars, then to blank.
//
//     xau.baseUrl=http://192.168.0.116:8765
//     xau.token=<the token the server was started with>
//
// These are baked into the DEBUG variant only. The release variant gets "" -- see buildTypes.
val localProps = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }
        ?.inputStream()?.use { load(it) }
}
fun devDefault(key: String, env: String): String =
    (localProps.getProperty(key) ?: System.getenv(env) ?: "").trim()

val devBaseUrl = devDefault("xau.baseUrl", "XAU_BASE_URL")
val devToken = devDefault("xau.token", "XAU_TOKEN")

// Demo build: the password for the client.p12 bundled in assets/certs/. Non-blank = the app loads
// the embedded certificate at startup, so the EC2 mTLS path works with no manual cert upload. The
// Connect screen is still shown (URL + token pre-filled); the user taps CONNECT. DEBUG only.
val devP12Password = devDefault("xau.p12Password", "XAU_P12_PASSWORD")

// The MASTER password for the default account (servers.json -> defaultAccount, currently the Exness
// demo 472200942). Baked into BuildConfig so the ACCOUNT form pre-fills it; sourced from
// local.properties (gitignored) so the secret lands in the APK but never in git. Blank -> the form
// pre-fills login + server only and the user types the password once.
val devAccountPassword = devDefault("xau.defaultAccountPassword", "XAU_DEFAULT_ACCOUNT_PASSWORD")

// Whether the RELEASE build also bakes the URL/token/p12-password/account-password and bundles the
// demo certs. DEFAULTS TO TRUE (baked into every build, per product decision) -- pass
// `-Pxau.embedSecrets=false` to build a secret-free, extraction-proof release (empty DEFAULT_* fields,
// no client.p12 packaged). Debug always bakes regardless.
//
// SECURITY: with this on, the release APK carries a live token + client cert + the demo account's
// password. Anyone holding the APK can `strings` them out and place orders on that (demo) account.
// The signature stops someone RE-signing a modified APK as us; it does NOT hide a baked-in string.
val embedSecrets = (project.findProperty("xau.embedSecrets") as String?)?.toBoolean() ?: true

// Personal-convenience escape hatch: sign the RELEASE variant with the DEBUG key. Android only
// updates an installed app IN PLACE when the new APK carries the SAME signing key; a debug build
// and a real-release build have different keys, so switching between them normally forces an
// uninstall -- which WIPES app data (saved server list, imported certs, UI prefs). With
// `-Pxau.debugSign=true` the release is signed with the debug key instead, so `adb install -r`
// updates the already-installed debug build in place and keeps every bit of that data.
//
// OFF by default: a normal `assembleRelease` keeps the real release identity. This is NOT for
// distribution -- a debug-signed APK carries the world-known debug signature, not ours.
val debugSign = (project.findProperty("xau.debugSign") as String?)?.toBoolean() ?: false

android {
    namespace = "com.xauorderpad"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.xauorderpad"

        // minSdk 34 (Android 14), NOT 31.
        //
        // The foreground service is `specialUse`, and that type -- along with the
        // FOREGROUND_SERVICE_SPECIAL_USE permission and
        // ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE -- all landed in API 34. At
        // minSdk 31 every one of those is a NewApi violation, and lint's NewApi is FATAL,
        // so `assembleRelease` fails. Version-guarding them all is possible but buys
        // nothing: there is one user and one phone, and it is on Android 14+.
        //
        // `specialUse` itself is non-negotiable -- see AndroidManifest.xml. dataSync is
        // capped at a cumulative 6h/24h on Android 15 and would silently sever the feed
        // mid-session.
        minSdk = 34
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
    }

    signingConfigs {
        if (hasSigning) {
            create("release") {
                storeFile = file(ksPath!!)
                storePassword = ksPass
                keyAlias = ksAlias
                keyPassword = keyPass
            }
        }
    }

    buildTypes {
        debug {
            // Pre-fill the Connect screen so the URL + token are not retyped on every install.
            buildConfigField("String", "DEFAULT_BASE_URL", "\"$devBaseUrl\"")
            buildConfigField("String", "DEFAULT_TOKEN", "\"$devToken\"")
            // Demo build: the password that unlocks the client.p12 bundled in assets/certs/.
            buildConfigField("String", "DEFAULT_P12_PASSWORD", "\"$devP12Password\"")
            // The default account's master password -> the ACCOUNT form pre-fills it.
            buildConfigField("String", "DEFAULT_ACCOUNT_PASSWORD", "\"$devAccountPassword\"")
        }
        release {
            // R8 off: ~15 files, nothing meaningful to shrink, and it only adds a way for
            // kotlinx.serialization's generated serializers to get stripped.
            isMinifyEnabled = false
            // Debug key when -Pxau.debugSign=true (in-place update over an installed debug build,
            // no data wipe); otherwise the real release key when it is configured.
            if (debugSign) signingConfig = signingConfigs.getByName("debug")
            else if (hasSigning) signingConfig = signingConfigs.getByName("release")

            // EMPTY unless -Pxau.embedSecrets=true. A signed release APK with a live trading token
            // compiled into it is a secret that cannot be rotated: anyone holding the APK can
            // `strings` it out, and the token grants order placement on a real account. So the
            // default (no flag) carries nothing; the flag is an explicit, temporary convenience for
            // handing a ready-to-run build to a tester -- and that build is exactly as extractable
            // as the debug one, minus `debuggable`. See `embedSecrets` above.
            buildConfigField("String", "DEFAULT_BASE_URL",     "\"${if (embedSecrets) devBaseUrl else ""}\"")
            buildConfigField("String", "DEFAULT_TOKEN",        "\"${if (embedSecrets) devToken else ""}\"")
            buildConfigField("String", "DEFAULT_P12_PASSWORD", "\"${if (embedSecrets) devP12Password else ""}\"")
            buildConfigField("String", "DEFAULT_ACCOUNT_PASSWORD", "\"${if (embedSecrets) devAccountPassword else ""}\"")
        }
    }

    // The demo certs (ca.crt + client.p12) live OUTSIDE src/main so they are NOT in every APK --
    // client.p12 is a trading credential. Debug always gets them (CertStore.seedFromAssetsIfEmpty
    // auto-loads them for on-desk testing); release gets them ONLY with -Pxau.embedSecrets=true. A
    // clean release ships no cert at all -> nothing to extract. The dir is gitignored.
    sourceSets {
        getByName("debug").assets.srcDir("src/demoCerts/assets")
        if (embedSecrets) getByName("release").assets.srcDir("src/demoCerts/assets")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    // buildConfig must be opted into explicitly on AGP 8+ (it used to be on by default).
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

kotlin {
    // `kotlinOptions { jvmTarget = ... }` is deprecated on Kotlin 2.x.
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime)
    implementation(libs.androidx.lifecycle.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    // No androidx.security:security-crypto -- see the note in libs.versions.toml.

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.graphics)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.foundation)   // used directly; do not rely on the transitive

    implementation(libs.okhttp)                        // WebSocket AND REST -- no Retrofit
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.collections.immutable) // stable list type for Compose
}
