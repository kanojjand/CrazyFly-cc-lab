plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "lab.cc.crazyfly"
    compileSdk = 34

    defaultConfig {
        applicationId = "lab.cc.crazyfly"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    // Свой debug-ключ в репозитории: APK с компьютера и с GitHub подписаны одинаково и ставятся друг поверх друга.
    // Это не секрет — пароль debug-ключа у всех "android"; для Google Play нужен был бы другой, закрытый ключ.
    signingConfigs {
        getByName("debug") {
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
