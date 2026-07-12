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
        }
        release {
            // R8 off: ~15 files, nothing meaningful to shrink, and it only adds a way for
            // kotlinx.serialization's generated serializers to get stripped.
            isMinifyEnabled = false
            if (hasSigning) signingConfig = signingConfigs.getByName("release")

            // EMPTY, deliberately. A signed release APK with a live trading token compiled into
            // it is a secret that cannot be rotated: anyone holding the APK can `strings` it out,
            // and the token grants order placement on a real account. Convenience belongs on the
            // dev build; the shippable artifact carries nothing.
            buildConfigField("String", "DEFAULT_BASE_URL", "\"\"")
            buildConfigField("String", "DEFAULT_TOKEN", "\"\"")
        }
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
