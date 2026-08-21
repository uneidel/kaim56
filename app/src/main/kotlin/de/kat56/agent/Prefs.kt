// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.content.Context

/** Einfache Einstellungs-Persistenz (SharedPreferences). */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("katagent", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("serverUrl", "https://agents.kat56.de") ?: ""
        set(v) = sp.edit().putString("serverUrl", v).apply()

    var instance: String
        get() = sp.getString("instance", "ortest") ?: ""
        set(v) = sp.edit().putString("instance", v).apply()

    // Welche Server-Instanz die Assistenten-Taste (ACTION_ASSIST) oeffnet.
    // Leer = die aktuell aktive Instanz verwenden.
    var assistInstance: String
        get() = sp.getString("assistInstance", "") ?: ""
        set(v) = sp.edit().putString("assistInstance", v).apply()

    var user: String
        get() = sp.getString("user", "admin") ?: ""
        set(v) = sp.edit().putString("user", v).apply()

    var pass: String
        get() = sp.getString("pass", "") ?: ""
        set(v) = sp.edit().putString("pass", v).apply()

    var modelPath: String
        get() = sp.getString("modelPath", "") ?: ""
        set(v) = sp.edit().putString("modelPath", v).apply()

    var mode: String
        get() = sp.getString("mode", "local") ?: "local"
        set(v) = sp.edit().putString("mode", v).apply()

    // Web-Zugriff im Gerät-Modus (App holt Suche/Seiten als Kontext für Gemma).
    var webAccess: Boolean
        get() = sp.getBoolean("webAccess", false)
        set(v) = sp.edit().putBoolean("webAccess", v).apply()

    // Aktives On-Device-Modell (Dateiname im models/-Ordner).
    var activeModel: String
        get() = sp.getString("activeModel", "") ?: ""
        set(v) = sp.edit().putString("activeModel", v).apply()

    // Zuletzt geoeffneter Chat (nach Neustart wiederherstellen).
    var currentChatId: String
        get() = sp.getString("currentChatId", "") ?: ""
        set(v) = sp.edit().putString("currentChatId", v).apply()

    // On-Device-Download (HuggingFace)
    var hfToken: String
        get() = sp.getString("hfToken", "") ?: ""
        set(v) = sp.edit().putString("hfToken", v).apply()

    var modelUrl: String
        get() = sp.getString("modelUrl",
            "https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm") ?: ""
        set(v) = sp.edit().putString("modelUrl", v).apply()
}

