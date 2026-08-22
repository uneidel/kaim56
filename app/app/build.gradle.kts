plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "de.kat56.agent"
    compileSdk = 34

    defaultConfig {
        applicationId = "de.kat56.agent"
        minSdk = 26
        targetSdk = 34
        versionCode = 69
        versionName = "5.19"
        ndk {
            // Xiaomi 15 = arm64-v8a. Only this ABI -> significantly smaller APK.
            abiFilters += "arm64-v8a"
        }
    }

    signingConfigs {
        // Fixed key -> every APK has the same signature -> update in place,
        // app storage (model) is preserved. Deliberately a simple dev password.
        create("stable") {
            storeFile = file("../keystore/katagent.jks")
            storePassword = "katagent"
            keyAlias = "katagent"
            keyPassword = "katagent"
        }
    }
    buildTypes {
        getByName("debug") {
            // R8 an: strippt ungenutzte MediaPipe-Vision-Klassen -> kleinere APK.
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.getByName("stable")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    buildFeatures {
        compose = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
        // Native libs COMPRESSED into the APK (instead of uncompressed) -> APK file
        // significantly smaller (under the 30 MB send limit). Unpacked on install
        // (a bit more storage / slower first start, otherwise uncritical).
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.2")
    implementation(platform("androidx.compose:compose-bom:2024.06.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    // Show/hide the full images and the attachment sheet (AnimatedVisibility).
    implementation("androidx.compose.animation:animation")
    implementation("androidx.compose.material3:material3")
    // Full Google Material icons (R8 removes unused ones -> hardly any size increase).
    implementation("androidx.compose.material:material-icons-extended")
    implementation("com.google.android.material:material:1.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    // On-Device-LLM via LiteRT-LM (laedt .litertlm-Modelle inkl. Gemma 4; multimodal).
    implementation("com.google.ai.edge.litertlm:litertlm-android:0.14.0")
}
