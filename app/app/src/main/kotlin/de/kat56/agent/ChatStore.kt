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

data class Msg(val user: Boolean, val text: String)

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
            for (m in c.messages) ma.put(JSONObject().put("user", m.user).put("text", m.text))
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
                        messages.add(Msg(m.getBoolean("user"), m.getString("text")))
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
