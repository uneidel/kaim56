// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.json.JSONArray
import android.net.Uri
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

/**
 * The manager's API, as one module: address and login live in the instance,
 * HTTP + Basic auth happen in connect() (also used by AppWebActivity), and the
 * error of a failed call is in [lastError] of THAT instance — one client per
 * call site, so parallel calls can no longer overwrite each other's error.
 * iroh:// works the same: IrohNet hangs it behind java.net.URL.
 */
class ManagerClient(baseUrl: String, private val user: String, private val pass: String) {
    constructor(prefs: Prefs) : this(prefs.serverUrl, prefs.user, prefs.pass)

    private val base = baseUrl.trimEnd('/')

    /** Why the last call on this client failed ("" after a success). */
    var lastError: String = ""
        private set

    /** An authenticated connection to `path` (already including any query). */
    fun connect(path: String, method: String = "GET", readTimeoutMs: Int = 30000): HttpURLConnection {
        val conn = URL(base + path).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 15000
        conn.readTimeout = readTimeoutMs
        if (user.isNotEmpty()) {
            val cred = java.util.Base64.getEncoder().encodeToString("$user:$pass".toByteArray())
            conn.setRequestProperty("Authorization", "Basic $cred")
        }
        return conn
    }

    fun listPersonas(): String? =
        request("GET", "/api/personas", null)


    fun listTasks(): String? =
        request("GET", "/api/tasks", null)

    fun createTask(instance: String, message: String, schedule: String): String? =
        request("POST", "/api/tasks",
            JSONObject().put("instance", instance).put("message", message).put("schedule", schedule).toString())

    fun deleteTask(id: String): String? =
        request("POST", "/api/tasks/$id/delete", "")



    /**
     * Aufnahme zum Manager schicken und den erkannten Text holen.
     * Der Manager reicht an den Sprachdienst durch (Parakeet); das Format ist
     * egal, dort wandelt ffmpeg auf 16-kHz-Mono.
     */
    fun stt(audio: ByteArray, mime: String): String? {
        val conn = connect("/api/stt", "POST", 120000)
        return try {
            conn.setRequestProperty("Content-Type", mime)
            conn.doOutput = true
            conn.outputStream.use { it.write(audio) }
            val code = conn.responseCode
            if (code !in 200..299) { lastError = "HTTP $code"; return null }
            lastError = ""
            JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optString("text").takeIf { it.isNotBlank() }
        } catch (e: Exception) {
            lastError = e.message ?: e.toString(); null
        } finally { conn.disconnect() }
    }

    /** Text sprechen lassen; liefert die WAV-Daten oder null. */
    fun tts(text: String): ByteArray? {
        val conn = connect("/api/tts", "POST", 120000)
        return try {
            conn.setRequestProperty("Content-Type", "application/json")
            conn.doOutput = true
            conn.outputStream.use { it.write(JSONObject().put("text", text).toString().toByteArray()) }
            val code = conn.responseCode
            if (code !in 200..299) { lastError = "HTTP $code"; return null }
            lastError = ""
            conn.inputStream.readBytes()
        } catch (e: Exception) {
            lastError = e.message ?: e.toString(); null
        } finally { conn.disconnect() }
    }

    fun pull(): String? =
        request("GET", "/api/chats", null)

    /** Ergebnis eines Chat-Long-Polls: `chats` ist null, wenn sich nichts getan hat. */
    data class ChatPoll(val rev: Long, val chats: String?, val tombstones: String?)

    /**
     * Long-Poll auf den gemeinsamen Chat-Store: der Manager antwortet erst, wenn
     * jemand (App ODER Web) schreibt — oder nach `waitSec` Sekunden ohne Inhalt.
     * Dadurch stehen fremde Nachrichten hier binnen Sekundenbruchteilen, ohne
     * Dauer-Polling. Aeltere Manager ohne `since`/`wait` liefern die blanke
     * Liste; das faengt der Parser ab und der Aufrufer faellt auf Warten zurueck.
     */
    fun pollChats(since: Long, waitSec: Int): ChatPoll? {
        val raw = request("GET", "/api/chats?since=$since&wait=$waitSec", null, waitSec * 1000 + 15000) ?: return null
        return try {
            val o = JSONObject(raw)
            ChatPoll(o.optLong("rev"), o.optJSONArray("chats")?.toString(),
                     o.optJSONObject("tombstones")?.toString())
        } catch (e: Exception) { null }
    }

    /** Ergebnis einer Dokument-Extraktion im Manager. */
    data class Extracted(val name: String, val text: String, val note: String)

    /**
     * PDF/DOCX/Text zum Manager schicken; zurueck kommt der reine TEXT.
     * Das Modell sieht nie die Binaerdatei — in den Chat wandert der Text.
     * Null = fehlgeschlagen (Grund in lastError).
     */
    fun extract(name: String, data: ByteArray): Extracted? {
        val enc = java.net.URLEncoder.encode(name, "UTF-8")
        val raw = request("POST", "/api/extract?name=$enc", null, rawBody = data) ?: return null
        return try {
            val o = JSONObject(raw)
            val err = o.optString("error")
            if (err.isNotBlank()) { lastError = err; return null }
            Extracted(o.optString("name", name), o.optString("text"), o.optString("note"))
        } catch (e: Exception) { lastError = e.message ?: "parse error"; null }
    }

    fun push(json: String): Boolean =
        request("POST", "/api/chats", json) != null

    /** Ein Schritt einer Mission. target = Instanz, an die der Schritt
     *  delegiert wurde (create_task-Ziel) — Plan und Ausfuehrung koennen auf
     *  verschiedenen Agenten liegen. */
    data class MissionStep(val n: Int, val text: String, val status: String,
                           val taskId: String, val target: String)

    /** Eine Mission: Plan + Fortschritt liegen im Manager. instance = Eigentuemer
     *  (der Agent, der geplant hat) — jeder Agent kann Missionen besitzen. */
    data class Mission(val id: String, val goal: String, val status: String,
                       val steps: List<MissionStep>, val summary: String, val lastLog: String,
                       val instance: String)

    private fun parseMission(m: JSONObject, inst: String): Mission {
        val sa = m.optJSONArray("steps")
        val steps = if (sa == null) emptyList() else (0 until sa.length()).map { j ->
            val st = sa.getJSONObject(j)
            MissionStep(st.optInt("n"), st.optString("text"), st.optString("status"),
                st.optString("task_id"), st.optString("target"))
        }
        val log = m.optJSONArray("log")
        return Mission(m.optString("id"), m.optString("goal"), m.optString("status"),
            steps, m.optString("summary"),
            if (log != null && log.length() > 0) log.optString(log.length() - 1) else "", inst)
    }

    /** Missionen ALLER Agenten (Admin-Sicht): der Manager liefert sie nach
     *  Eigentuemer gruppiert (by_instance). `missions` ist der Fallback fuer
     *  aeltere Manager, die nur die des Orchestrators kannten. */
    fun listMissions(): List<Mission>? {
        val raw = request("GET", "/api/missions", null)
            ?: return null
        return try {
            val o = JSONObject(raw)
            val by = o.optJSONObject("by_instance")
            if (by != null) {
                val out = mutableListOf<Mission>()
                for (inst in by.keys()) {
                    val arr = by.optJSONArray(inst) ?: continue
                    for (i in 0 until arr.length()) out.add(parseMission(arr.getJSONObject(i), inst))
                }
                out
            } else {
                val arr = o.optJSONArray("missions") ?: return emptyList()
                (0 until arr.length()).map { parseMission(arr.getJSONObject(it), "orchestrator") }
            }
        } catch (e: Exception) { null }
    }

    /** pause | resume | abort einer Mission (Admin). Die Instanz muss mit, weil
     *  Missionen jedem Agenten gehoeren koennen (leer = Manager sucht selbst). */
    fun missionAction(id: String,
                      action: String, instance: String = ""): Boolean =
        request("POST", "/api/mission-admin",
            JSONObject().put("id", id).put("action", action)
                .put("instance", instance).toString()) != null

    /** A skill an agent distilled after a long successful turn, awaiting review. */
    data class SkillProposal(val id: String, val name: String, val instance: String,
                             val description: String, val content: String, val update: Boolean)

    /** Pending skill proposals (admin view). null on error. */
    fun listSkillProposals(): List<SkillProposal>? {
        val raw = request("GET", "/api/skill-proposals", null) ?: return null
        return try {
            val arr = JSONObject(raw).optJSONArray("proposals") ?: return emptyList()
            (0 until arr.length()).map {
                val o = arr.getJSONObject(it)
                SkillProposal(o.optString("id"), o.optString("name"), o.optString("instance"),
                    o.optString("description"), o.optString("content"), o.optBoolean("update"))
            }
        } catch (e: Exception) { null }
    }

    /** approve = add the skill to the library; false = discard the proposal. */
    fun decideSkillProposal(id: String, approve: Boolean): Boolean =
        request("POST", "/api/skill-proposals/${Uri.encode(id)}/${if (approve) "approve" else "discard"}", "") != null

    /** Trace eines Turns: {turn:{…}, llm:[…], tools:[…]} vom Manager, null bei Fehler. */
    fun trace(instance: String, turn: String): JSONObject? {
        val raw = request("GET", "/api/trace/${Uri.encode(instance)}?turn=${Uri.encode(turn)}", null) ?: return null
        return try { JSONObject(raw) } catch (e: Exception) { null }
    }

    /** Prompt-Templates (Slash-Kommandos) vom Manager. */
    fun listPrompts(): List<Pair<String, String>> {
        val raw = request("GET", "/api/prompts", null)
            ?: return emptyList()
        return try {
            val arr = JSONObject(raw).optJSONArray("prompts") ?: return emptyList()
            (0 until arr.length()).map { i ->
                val o = arr.getJSONObject(i)
                o.optString("name") to o.optString("text")
            }
        } catch (e: Exception) { emptyList() }
    }

    /** Nachricht in einen LAUFENDEN Turn einspeisen (Steering). true = queued. */
    fun steer(instance: String, message: String): Boolean {
        val raw = request("POST", "/i/$instance/api/steer",
            JSONObject().put("message", message).toString()) ?: return false
        return try { JSONObject(raw).optBoolean("queued") } catch (e: Exception) { false }
    }

    /** Eine Push-Benachrichtigung aus dem Manager. */
    data class NotifItem(val id: String, val ts: Long, val title: String,
                         val body: String, val instance: String, val read: Boolean,
                         val link: String = "")

    /** Ergebnis des Notification-Long-Polls: `items` ist null bei Zeitablauf. */
    data class NotifPoll(val rev: Long, val items: List<NotifItem>?, val unread: Int)

    /** Long-Poll auf /api/notifications — analog zu pollChats. */
    fun pollNotifications(since: Long, waitSec: Int): NotifPoll? {
        val raw = request("GET", "/api/notifications?since=$since&wait=$waitSec", null, waitSec * 1000 + 15000) ?: return null
        return try {
            val o = JSONObject(raw)
            val arr = o.optJSONArray("notifications")
            val items = if (arr == null) null else (0 until arr.length()).map {
                val n = arr.getJSONObject(it)
                NotifItem(n.optString("id"), n.optLong("ts"), n.optString("title"),
                    n.optString("body"), n.optString("instance"), n.optBoolean("read"),
                    n.optString("link"))
            }
            NotifPoll(o.optLong("rev"), items, o.optInt("unread"))
        } catch (e: Exception) { null }
    }

    /** Alle Benachrichtigungen als gelesen quittieren. */
    fun markNotifRead(): Boolean =
        request("POST", "/api/notifications/read", "{\"all\":true}") != null

    /** Security Gateway: welche Chats gefiltert werden und wieviel bisher
     *  entfernt wurde. Der Zustand liegt am Manager, nicht im Geraet — sonst
     *  waere er in App und Web verschieden. */
    data class Gateway(val on: Set<String>, val chars: Map<String, Int>, val images: Map<String, Int>,
                       val available: Boolean)

    fun gatewayGet(): Gateway? {
        val raw = request("GET", "/api/gateway", null) ?: return null
        return try {
            val o = JSONObject(raw)
            val on = mutableSetOf<String>()
            o.optJSONObject("chats")?.let { c -> c.keys().forEach { if (c.optBoolean(it)) on.add(it) } }
            val chars = mutableMapOf<String, Int>(); val imgs = mutableMapOf<String, Int>()
            o.optJSONObject("stats")?.let { s ->
                s.keys().forEach { k ->
                    val e = s.optJSONObject(k) ?: return@forEach
                    chars[k] = e.optInt("in") + e.optInt("out")
                    imgs[k] = e.optInt("img")
                }
            }
            Gateway(on, chars, imgs, o.optBoolean("available"))
        } catch (e: Exception) { null }
    }

    fun gatewaySet(chatId: String, on: Boolean): Boolean =
        request("POST", "/api/gateway",
            JSONObject().put("chat", chatId).put("on", on).toString()) != null

    /** The manager's apps (GET /api/apps) and every Worker on its Cloudflare
     *  account (GET /api/apps/cloudflare) — see AppsCatalog. */
    fun listApps(): String? =
        request("GET", "/api/apps", null)

    fun listCfWorkers(refresh: Boolean = false): String? =
        request("GET", "/api/apps/cloudflare" + if (refresh) "?refresh=1" else "", null)

    fun listInstances(): String? =
        request("GET", "/api/instances", null)

    fun action(name: String, act: String): String? =
        request("POST", "/api/instances/$name/$act", "")

    /** Server-Agent (Manager-Instanz) anlegen + starten. Gibt eine Status-Meldung. */
    fun createAndStart(
        name: String, template: String, config: JSONObject,
    ): String {
        val cBody = JSONObject().put("name", name).put("template", template).put("config", config).toString()
        val c = request("POST", "/api/create", cBody)
            ?: return "⚠️ Anlegen fehlgeschlagen: $lastError"
        val cMsg = try { JSONObject(c).optString("msg", c) } catch (e: Exception) { c }
        // The manager answers failures with HTTP 200 + {msg}: "'x' already exists",
        // "invalid name", "unknown template 'y'" (it switched to English; the old
        // German check let a failed create go on to start)
        if (msgFailed(cMsg)) return "⚠️ $cMsg"
        val s = request("POST", "/api/instances/$name/start", "")
            ?: return "$cMsg · ⚠️ Start fehlgeschlagen: $lastError"
        val sMsg = try { JSONObject(s).optString("msg", s) } catch (e: Exception) { s }
        return "$cMsg · $sMsg"
    }

    private fun request(method: String, path: String, body: String?,
                        readTimeoutMs: Int = 30000, rawBody: ByteArray? = null): String? {
        val conn = connect(path, method, readTimeoutMs)
        return try {
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray()) }
            } else if (rawBody != null) {
                // Binaerupload (Dokument-Extraktion): Bytes unveraendert.
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/octet-stream")
                conn.outputStream.use { it.write(rawBody) }
            }
            val code = conn.responseCode
            if (code !in 200..299) {
                lastError = "HTTP $code" + when (code) {
                    401 -> " (Benutzer/Passwort prüfen)"
                    404 -> " (Endpunkt/Instanz nicht gefunden)"
                    else -> ""
                }
                return null
            }
            lastError = ""
            conn.inputStream.bufferedReader().use { it.readText() }
        } catch (e: Exception) {
            lastError = e.message ?: e.toString()
            null
        } finally {
            conn.disconnect()
        }
    }

    companion object {
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

        /** A manager msg route answers HTTP 200 even when it failed; the text says so. */
        fun msgFailed(m: String): Boolean {
            val l = m.trim().lowercase()
            return listOf("error", "unknown", "invalid", "??", "cannot ").any { l.startsWith(it) } ||
                listOf(" missing", "not allowed", "already exists").any { l.contains(it) }
        }
    }
}
