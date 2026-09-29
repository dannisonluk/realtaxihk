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
        val properties = java.util.Properties()
        val file = rootProject.file("local.properties")
        if (file.exists()) {
            file.inputStream().use { properties.load(it) }
        }
        properties.getProperty("GOOGLE_MAPS_API_KEY") ?: ""
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

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
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
