// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

private val msgKeySeq = java.util.concurrent.atomic.AtomicLong(1)
// key: stabile Identitaet EINER Nachricht (bleibt ueber copy() erhalten), damit
// Streaming-Chunks per Key statt per Positions-Index ans Ziel gehen -> keine
// verrutschten Indizes bei Interrupt/Sync. key ist bewusst NICHT Teil von
// equals/hashCode, sonst wuerde der Multi-Device-Prefix-Merge (Vergleich per
// Inhalt) brechen.
data class Msg(val user: Boolean, val text: String, val key: Long = msgKeySeq.getAndIncrement(), val image: String? = null,
               val turn: String? = null) {          // turn id from the bridge -> trace (server chats)
    override fun equals(other: Any?) = other is Msg && other.user == user && other.text == text
    override fun hashCode() = user.hashCode() * 31 + text.hashCode()
}

/** Eine Konversation. title/updatedAt sind Compose-State -> UI aktualisiert sich. */
class Conversation(
    val id: String = UUID.randomUUID().toString(),
    title: String = "Neuer Chat",
    mode: String = "local",
    instance: String = "",
    updatedAt: Long = 0L,
) {
    var title by mutableStateOf(title)
    var mode by mutableStateOf(mode)
    var instance by mutableStateOf(instance)   // gewählter Server-Agent für diesen Chat
    var updatedAt by mutableStateOf(updatedAt)
    val messages = mutableStateListOf<Msg>()
}

/** Lokale Persistenz (JSON) fuer Konversationen + Registry der Modelle. */
class ChatStore(context: Context) {
    private val file = File(context.filesDir, "conversations.json")
    val modelsDir: File = File(context.filesDir, "models").apply { mkdirs() }
    private val tombFile = File(context.filesDir, "chat_tombstones.json")
    private val tombTtlMs = 60L * 24 * 3600 * 1000   // Loesch-Marker nach 60 Tagen verwerfen

    /** Loesch-Tombstones {id -> deletedAt} laden. */
    fun loadTombs(): MutableMap<String, Long> = try {
        if (tombFile.exists()) {
            val o = JSONObject(tombFile.readText())
            val m = HashMap<String, Long>()
            o.keys().forEach { m[it] = o.optLong(it) }
            m
        } else HashMap()
    } catch (_: Exception) { HashMap() }

    /** Tombstones speichern (mit TTL-Prune). */
    fun saveTombs(t: Map<String, Long>) {
        val now = System.currentTimeMillis()
        val o = JSONObject()
        for ((k, v) in t) if (now - v < tombTtlMs) o.put(k, v)
        try { tombFile.writeText(o.toString()) } catch (_: Exception) {}
    }

    /** Push-Payload fuer den Server: {chats:[...], tombstones:{id:deletedAt}}. */
    fun toPushJson(conversations: List<Conversation>, tombs: Map<String, Long>): String {
        val to = JSONObject()
        for ((k, v) in tombs) to.put(k, v)
        return JSONObject()
            .put("chats", JSONArray(toJson(conversations)))
            .put("tombstones", to).toString()
    }

    fun load(): List<Conversation> =
        if (file.exists()) fromJson(file.readText()) else emptyList()

    fun save(conversations: List<Conversation>) {
        try {
            file.writeText(toJson(conversations))
        } catch (_: Exception) {
        }
    }

    /** Konversationen -> JSON-String (fuer Server-Sync). */
    fun toJson(conversations: List<Conversation>): String {
        val arr = JSONArray()
        for (c in conversations) {
            val o = JSONObject()
                .put("id", c.id).put("title", c.title).put("mode", c.mode)
                .put("instance", c.instance).put("updatedAt", c.updatedAt)
            val ma = JSONArray()
            for (m in c.messages) ma.put(JSONObject().put("user", m.user).put("text", m.text).apply {
                if (m.image != null) put("image", m.image)
                if (m.turn != null) put("turn", m.turn)
            })
            o.put("messages", ma)
            arr.put(o)
        }
        return arr.toString()
    }

    /** JSON-String -> Konversationen. */
    fun fromJson(str: String): List<Conversation> {
        return try {
            val arr = JSONArray(str)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Conversation(
                    id = o.getString("id"),
                    title = o.optString("title", "Chat"),
                    mode = o.optString("mode", "local"),
                    instance = o.optString("instance", ""),
                    updatedAt = o.optLong("updatedAt", 0),
                ).apply {
                    val ma = o.getJSONArray("messages")
                    for (j in 0 until ma.length()) {
                        val m = ma.getJSONObject(j)
                        messages.add(Msg(m.getBoolean("user"), m.getString("text"), image = m.optString("image", "").ifEmpty { null },
                            turn = m.optString("turn", "").ifEmpty { null }))
                    }
                }
            }
        } catch (e: Exception) {
            emptyList()
        }
    }

    // ---- Modelle -----------------------------------------------------------
    fun models(): List<File> =
        (modelsDir.listFiles { f -> f.isFile && f.name.endsWith(".litertlm") } ?: emptyArray())
            .sortedBy { it.name }

    fun modelFile(name: String) = File(modelsDir, name)

    /** Altes v1.0-Modell (filesDir/model.litertlm) in den models/-Ordner uebernehmen. */
    fun migrate(prefs: Prefs) {
        val old = File(modelsDir.parentFile, "model.litertlm")
        if (old.exists() && old.length() > 0 && models().isEmpty()) {
            val dest = File(modelsDir, "modell.litertlm")
            if (old.renameTo(dest)) prefs.activeModel = dest.name
        }
    }
}
