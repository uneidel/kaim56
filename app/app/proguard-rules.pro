# LiteRT-LM (On-Device-LLM) — via JNI/Reflection genutzt, komplett behalten.
-keep class com.google.ai.edge.litertlm.** { *; }
-keep class com.google.ai.edge.litert.** { *; }
-dontwarn com.google.ai.edge.**

# Protobuf-lite (evtl. transitiv)
-keepclassmembers class * extends com.google.protobuf.GeneratedMessageLite {
    <fields>;
}
-dontwarn com.google.protobuf.**

# Build-Zeit-Only-Klassen (auto-value/javapoet) ignorieren
-dontwarn javax.annotation.processing.**
-dontwarn javax.lang.model.**
-dontwarn javax.annotation.**
-dontwarn com.google.auto.value.**
-dontwarn autovalue.shaded.**

-keepattributes *Annotation*, Signature, InnerClasses, EnclosingMethod
