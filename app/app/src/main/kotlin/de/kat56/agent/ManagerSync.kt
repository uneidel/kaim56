package de.kat56.agent

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Ein Server-Agent (Manager-Instanz). */
data class AgentInstance(
    val name: String,
    val running: Boolean,
    val template: String,
    val transport: String,
    val model: String,
)

/** Eine Hintergrundaufgabe (Manager-Task). */
data class AgentTask(
    val id: String,
    val instance: String,
    val message: String,
    val status: String,
    val result: String,
    val schedule: String,
    val updated: Long,
)

/** Eine Persona (benannter System-Prompt). */
data class Persona(val name: String, val prompt: String)

/** Chat-Sync mit dem Manager: GET/POST {base}/api/chats (Basic-Auth). */
object ManagerSync {

    fun listPersonas(baseUrl: String, user: String, pass: String): String? =
        request("GET", "${baseUrl.trimEnd('/')}/api/personas", user, pass, null)

    fun parsePersonas(json: String?): List<Persona> {
        if (json == null) return emptyList()
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                Persona(o.optString("name"), o.optString("prompt"))
            }
        } catch (e: Exception) { emptyList() }
    }

    fun listTasks(baseUrl: String, user: String, pass: String): String? =
        request("GET", "${baseUrl.trimEnd('/')}/api/tasks", user, pass, null)

    fun createTask(baseUrl: String, user: String, pass: String,
                   instance: String, message: String, schedule: String): String? =
        request("POST", "${baseUrl.trimEnd('/')}/api/tasks", user, pass,
            JSONObject().put("instance", instance).put("message", message).put("schedule", schedule).toString())

    fun deleteTask(baseUrl: String, user: String, pass: String, id: String): String? =
        request("POST", "${baseUrl.trimEnd('/')}/api/tasks/$id/delete", user, pass, "")

    fun parseTasks(json: String?): List<AgentTask> {
        if (json == null) return emptyList()
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                AgentTask(
                    id = o.optString("id"), instance = o.optString("instance"),
                    message = o.optString("message"), status = o.optString("status"),
                    result = o.optString("result"), schedule = o.optString("schedule"),
                    updated = o.optLong("updated"),
                )
            }.reversed()
        } catch (e: Exception) { emptyList() }
    }

    /** Ergebnis des letzten request() – für aussagekräftige Fehlermeldungen. */
    var lastStatus: String = ""
        private set

    fun parseInstances(json: String?): List<AgentInstance> {
        if (json == null) return emptyList()
        return try {
            val arr = JSONArray(json)
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                val cfg = o.optJSONObject("config") ?: JSONObject()
                AgentInstance(
                    name = o.optString("name"),
                    running = o.optBoolean("running"),
                    template = o.optString("template", ""),
                    transport = cfg.optString("TRANSPORT", ""),
                    model = cfg.optString("OPENROUTER_MODEL", cfg.optString("PI_MODEL", cfg.optString("PRIME_MODEL", ""))),
                )
            }
        } catch (e: Exception) { emptyList() }
    }

    fun pull(baseUrl: String, user: String, pass: String): String? =
        request("GET", "${baseUrl.trimEnd('/')}/api/chats", user, pass, null)

    /** Ergebnis eines Chat-Long-Polls: `chats` ist null, wenn sich nichts getan hat. */
    data class ChatPoll(val rev: Long, val chats: String?)

    /**
     * Long-Poll auf den gemeinsamen Chat-Store: der Manager antwortet erst, wenn
     * jemand (App ODER Web) schreibt — oder nach `waitSec` Sekunden ohne Inhalt.
     * Dadurch stehen fremde Nachrichten hier binnen Sekundenbruchteilen, ohne
     * Dauer-Polling. Aeltere Manager ohne `since`/`wait` liefern die blanke
     * Liste; das faengt der Parser ab und der Aufrufer faellt auf Warten zurueck.
     */
    fun pollChats(baseUrl: String, user: String, pass: String, since: Long, waitSec: Int): ChatPoll? {
        val raw = request("GET", "${baseUrl.trimEnd('/')}/api/chats?since=$since&wait=$waitSec",
            user, pass, null, waitSec * 1000 + 15000) ?: return null
        return try {
            val o = JSONObject(raw)
            ChatPoll(o.optLong("rev"), o.optJSONArray("chats")?.toString())
        } catch (e: Exception) { null }
    }

    fun push(baseUrl: String, user: String, pass: String, json: String): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/chats", user, pass, json) != null

    fun listInstances(baseUrl: String, user: String, pass: String): String? =
        request("GET", "${baseUrl.trimEnd('/')}/api/instances", user, pass, null)

    fun action(baseUrl: String, user: String, pass: String, name: String, act: String): String? =
        request("POST", "${baseUrl.trimEnd('/')}/api/instances/$name/$act", user, pass, "")

    /** Server-Agent (Manager-Instanz) anlegen + starten. Gibt eine Status-Meldung. */
    fun createAndStart(
        baseUrl: String, user: String, pass: String,
        name: String, template: String, config: JSONObject,
    ): String {
        val base = baseUrl.trimEnd('/')
        val cBody = JSONObject().put("name", name).put("template", template).put("config", config).toString()
        val c = request("POST", "$base/api/create", user, pass, cBody)
            ?: return "⚠️ Anlegen fehlgeschlagen: $lastStatus"
        val cMsg = try { JSONObject(c).optString("msg", c) } catch (e: Exception) { c }
        // Der Manager antwortet auch bei Fehlern mit HTTP 200 + {msg:"…fehlgeschlagen/existiert…"}
        if (cMsg.contains("existiert") || cMsg.contains("ungültig") || cMsg.contains("unbekannt"))
            return "⚠️ $cMsg"
        val s = request("POST", "$base/api/instances/$name/start", user, pass, "")
            ?: return "$cMsg · ⚠️ Start fehlgeschlagen: $lastStatus"
        val sMsg = try { JSONObject(s).optString("msg", s) } catch (e: Exception) { s }
        return "$cMsg · $sMsg"
    }

    private fun request(method: String, url: String, user: String, pass: String, body: String?,
                        readTimeoutMs: Int = 30000): String? {
        val conn = URL(url).openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = method
            conn.connectTimeout = 15000
            conn.readTimeout = readTimeoutMs
            if (user.isNotEmpty()) {
                val cred = Base64.encodeToString("$user:$pass".toByteArray(), Base64.NO_WRAP)
                conn.setRequestProperty("Authorization", "Basic $cred")
            }
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = conn.responseCode
            if (code !in 200..299) {
                lastStatus = "HTTP $code" + when (code) {
                    401 -> " (Benutzer/Passwort prüfen)"
                    404 -> " (Endpunkt/Instanz nicht gefunden)"
                    else -> ""
                }
                return null
            }
            lastStatus = "OK"
            conn.inputStream.bufferedReader().use { it.readText() }
        } catch (e: Exception) {
            lastStatus = e.message ?: e.toString()
            null
        } finally {
            conn.disconnect()
        }
    }
}
