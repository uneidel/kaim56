// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
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

    /**
     * Aufnahme zum Manager schicken und den erkannten Text holen.
     * Der Manager reicht an den Sprachdienst durch (Parakeet); das Format ist
     * egal, dort wandelt ffmpeg auf 16-kHz-Mono.
     */
    fun stt(baseUrl: String, user: String, pass: String, audio: ByteArray, mime: String): String? {
        val conn = URL("${baseUrl.trimEnd('/')}/api/stt").openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15000
            conn.readTimeout = 120000
            if (user.isNotEmpty()) {
                val cred = Base64.encodeToString("$user:$pass".toByteArray(), Base64.NO_WRAP)
                conn.setRequestProperty("Authorization", "Basic $cred")
            }
            conn.setRequestProperty("Content-Type", mime)
            conn.doOutput = true
            conn.outputStream.use { it.write(audio) }
            val code = conn.responseCode
            if (code !in 200..299) { lastStatus = "HTTP $code"; return null }
            lastStatus = "OK"
            JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                .optString("text").takeIf { it.isNotBlank() }
        } catch (e: Exception) {
            lastStatus = e.message ?: e.toString(); null
        } finally { conn.disconnect() }
    }

    /** Text sprechen lassen; liefert die WAV-Daten oder null. */
    fun tts(baseUrl: String, user: String, pass: String, text: String): ByteArray? {
        val conn = URL("${baseUrl.trimEnd('/')}/api/tts").openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15000
            conn.readTimeout = 120000
            if (user.isNotEmpty()) {
                val cred = Base64.encodeToString("$user:$pass".toByteArray(), Base64.NO_WRAP)
                conn.setRequestProperty("Authorization", "Basic $cred")
            }
            conn.setRequestProperty("Content-Type", "application/json")
            conn.doOutput = true
            conn.outputStream.use { it.write(JSONObject().put("text", text).toString().toByteArray()) }
            val code = conn.responseCode
            if (code !in 200..299) { lastStatus = "HTTP $code"; return null }
            lastStatus = "OK"
            conn.inputStream.readBytes()
        } catch (e: Exception) {
            lastStatus = e.message ?: e.toString(); null
        } finally { conn.disconnect() }
    }

    fun pull(baseUrl: String, user: String, pass: String): String? =
        request("GET", "${baseUrl.trimEnd('/')}/api/chats", user, pass, null)

    /** Ergebnis eines Chat-Long-Polls: `chats` ist null, wenn sich nichts getan hat. */
    data class ChatPoll(val rev: Long, val chats: String?, val tombstones: String?)

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
            ChatPoll(o.optLong("rev"), o.optJSONArray("chats")?.toString(),
                     o.optJSONObject("tombstones")?.toString())
        } catch (e: Exception) { null }
    }

    /** Result of a document extraction in the manager. */
    data class Extracted(val name: String, val text: String, val note: String)

    /**
     * Send a PDF/DOCX/text file to the manager; back comes the plain TEXT.
     * The model never sees the binary — the text travels into the chat.
     * Null = failed (reason in lastStatus).
     */
    fun extract(baseUrl: String, user: String, pass: String,
                name: String, data: ByteArray): Extracted? {
        val enc = java.net.URLEncoder.encode(name, "UTF-8")
        val raw = request("POST", "${'$'}{baseUrl.trimEnd('/')}/api/extract?name=${'$'}enc",
            user, pass, null, rawBody = data) ?: return null
        return try {
            val o = JSONObject(raw)
            val err = o.optString("error")
            if (err.isNotBlank()) { lastStatus = err; return null }
            Extracted(o.optString("name", name), o.optString("text"), o.optString("note"))
        } catch (e: Exception) { lastStatus = e.message ?: "parse error"; null }
    }

    fun push(baseUrl: String, user: String, pass: String, json: String): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/chats", user, pass, json) != null

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
    fun listMissions(baseUrl: String, user: String, pass: String): List<Mission>? {
        val raw = request("GET", "${baseUrl.trimEnd('/')}/api/missions", user, pass, null)
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
    fun missionAction(baseUrl: String, user: String, pass: String, id: String,
                      action: String, instance: String = ""): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/mission-admin", user, pass,
            JSONObject().put("id", id).put("action", action)
                .put("instance", instance).toString()) != null

    /** Prompt-Templates (Slash-Kommandos) vom Manager. */
    fun listPrompts(baseUrl: String, user: String, pass: String): List<Pair<String, String>> {
        val raw = request("GET", "${baseUrl.trimEnd('/')}/api/prompts", user, pass, null)
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
    fun steer(baseUrl: String, user: String, pass: String, instance: String, message: String): Boolean {
        val raw = request("POST", "${baseUrl.trimEnd('/')}/i/$instance/api/steer", user, pass,
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
    fun pollNotifications(baseUrl: String, user: String, pass: String,
                          since: Long, waitSec: Int): NotifPoll? {
        val raw = request("GET", "${baseUrl.trimEnd('/')}/api/notifications?since=$since&wait=$waitSec",
            user, pass, null, waitSec * 1000 + 15000) ?: return null
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
    fun markNotifRead(baseUrl: String, user: String, pass: String): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/notifications/read", user, pass, "{\"all\":true}") != null

    /** Security Gateway: welche Chats gefiltert werden und wieviel bisher
     *  entfernt wurde. Der Zustand liegt am Manager, nicht im Geraet — sonst
     *  waere er in App und Web verschieden. */
    data class Gateway(val on: Set<String>, val chars: Map<String, Int>, val images: Map<String, Int>,
                       val available: Boolean)

    fun gatewayGet(baseUrl: String, user: String, pass: String): Gateway? {
        val raw = request("GET", "${baseUrl.trimEnd('/')}/api/gateway", user, pass, null) ?: return null
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

    fun gatewaySet(baseUrl: String, user: String, pass: String, chatId: String, on: Boolean): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/gateway", user, pass,
            JSONObject().put("chat", chatId).put("on", on).toString()) != null

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
                        readTimeoutMs: Int = 30000, rawBody: ByteArray? = null): String? {
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
            } else if (rawBody != null) {
                // Binary upload (document extraction): bytes as they are.
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/octet-stream")
                conn.outputStream.use { it.write(rawBody) }
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
