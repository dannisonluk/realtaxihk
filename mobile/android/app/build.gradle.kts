// `Properties` must be imported, not written as `java.util.Properties`.
//
// In a *project* script, the `java` extension accessor (JavaPluginExtension,
// contributed by AGP) shadows the `java` package name, so the qualified form
// fails to compile with "Unresolved reference 'util'" — and then the following
// line reports a misleading "Cannot infer type for this parameter".
//
// The identical expression in settings.gradle.kts is fine, because a Settings
// script has no `java` accessor. That asymmetry is why the Flutter template gets
// away with `java.util.Properties()` there but this script cannot.
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Google Maps key for the Android manifest.
//
// It is read from android/local.properties (gitignored), the same file the
// Flutter tool writes `flutter.sdk` into — so there is one place to put it and
// nothing to leak. An empty value is fine: AndroidManifest.xml has a placeholder
// and the Dart side detects the missing key and renders a coordinate panel
// instead of a map, so a keyless build still runs.
//
//   echo 'GOOGLE_MAPS_API_KEY=AIza...' >> android/local.properties
//
// The same value must also be passed to Dart at build time, since the
// google_maps_flutter plugin reads it there for non-Android platforms:
//
//   flutter run --dart-define=GOOGLE_MAPS_API_KEY=AIza...
val googleMapsApiKey: String =
    run {
        val properties = Properties()
        val file = rootProject.file("local.properties")
        if (file.exists()) {
            file.inputStream().use { properties.load(it) }
        }
        properties.getProperty("GOOGLE_MAPS_API_KEY") ?: ""
    }

// --- Release signing ---------------------------------------------------------
//
// Credentials live in android/key.properties, which is gitignored — the same
// place the Maps key goes, so there is one file to provision and nothing to
// leak. The shape is the one the Flutter docs use:
//
//   storeFile=/path/to/upload-keystore.jks
//   storePassword=…
//   keyAlias=upload
//   keyPassword=…
//
// Generate the keystore once and keep it out of the repo:
//
//   keytool -genkey -v -keystore upload-keystore.jks -keyalg RSA \
//     -keysize 2048 -validity 10000 -alias upload
//
// While key.properties is absent the release build falls back to the debug keys,
// so the build still runs on a machine that has no keystore. That APK is **not
// shippable** — it cannot be uploaded, and it cannot update a published build
// because the signature does not match. So a release task warns rather than
// producing it quietly.
val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
val hasReleaseKeystore = keystorePropertiesFile.exists()
if (hasReleaseKeystore) {
    keystorePropertiesFile.inputStream().use { keystoreProperties.load(it) }
}
if (!hasReleaseKeystore &&
    gradle.startParameter.taskNames.any { it.contains("release", ignoreCase = true) }
) {
    logger.warn(
        "android/key.properties is missing: this release build will be signed with " +
            "the DEBUG keys and cannot be published. See mobile/README.md.",
    )
}

android {
    namespace = "hk.realtaxi.mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "hk.realtaxi.mobile"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName

        manifestPlaceholders["googleMapsApiKey"] = googleMapsApiKey
    }

    signingConfigs {
        // Only created when the credentials are present. Resolving an absent
        // signing config throws, which would break every build on a machine that
        // has no keystore — including CI.
        if (hasReleaseKeystore) {
            create("release") {
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
                storeFile =
                    keystoreProperties.getProperty("storeFile")?.let { rootProject.file(it) }
                storePassword = keystoreProperties.getProperty("storePassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig =
                if (hasReleaseKeystore) {
                    signingConfigs.getByName("release")
                } else {
                    signingConfigs.getByName("debug")
                }
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
