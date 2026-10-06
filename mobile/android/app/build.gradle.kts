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
// A release APK signed with the debug keys is **not shippable**: it cannot be
// uploaded, and it cannot update a published build because the signature does not
// match. So a release build without a keystore fails outright rather than
// producing one quietly — a silent debug-signed release is worse than no release,
// because it looks like it worked.
val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
val hasReleaseKeystore = keystorePropertiesFile.exists()
if (hasReleaseKeystore) {
    keystorePropertiesFile.inputStream().use { keystoreProperties.load(it) }
}

// Does the requested Gradle invocation produce a release artifact? This matches
// the release-specific tasks (`assembleRelease`, `bundleRelease`, …) and the
// aggregate tasks that include them (`assemble`, `bundle`, `build`). It does *not*
// match `assembleDebug`, so a debug-only build still runs on a machine that has no
// keystore — which is what CI does (`flutter build apk --debug`).
val releaseRequested =
    gradle.startParameter.taskNames.any { task ->
        val leaf = task.substringAfterLast(':').lowercase()
        leaf.contains("release") || leaf == "assemble" || leaf == "bundle" || leaf == "build"
    }
if (!hasReleaseKeystore && releaseRequested) {
    throw GradleException(
        "android/key.properties is missing, so there is no release signing key. A " +
            "debug-signed release APK cannot be uploaded and cannot update a published " +
            "build. Create android/key.properties with storeFile / storePassword / " +
            "keyAlias / keyPassword (see mobile/README.md), or build a debug variant instead.",
    )
}

android {
    namespace = "com.hkfastdc.mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.hkfastdc.mobile"
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
            // R8 (shrink + obfuscate) and resource shrinking, stated explicitly
            // rather than left implicit.
            //
            // Flutter 3.44's Gradle plugin *does* set these for the release type
            // (`FlutterPlugin.kt`: `releaseBuildType.isMinifyEnabled = true`), but
            // only while `shouldShrinkResources` is true — and `-Pshrink=false`
            // flips that off, silently producing an unminified, un-obfuscated
            // release. Stating it here means the shipped artifact is shrunk no
            // matter how Gradle was invoked.
            //
            // `proguard-rules.pro` carries the app/plugin keeps the plugin's own
            // `flutter_proguard_rules.pro` does not cover; see that file for why
            // each rule exists.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig =
                if (hasReleaseKeystore) {
                    signingConfigs.getByName("release")
                } else {
                    // A release task would have thrown above, so reaching here means
                    // this is a debug-only build; the debug config keeps it
                    // configuring. A release build never lands in this branch.
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
