// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** A server agent (manager instance). */
data class AgentInstance(
    val name: String,
    val running: Boolean,
    val template: String,
    val transport: String,
    val model: String,
)

/** A background task (manager task). */
data class AgentTask(
    val id: String,
    val instance: String,
    val message: String,
    val status: String,
    val result: String,
    val schedule: String,
    val updated: Long,
)

/** A persona (named system prompt). */
data class Persona(val name: String, val prompt: String)

/** Chat sync with the manager: GET/POST {base}/api/chats (Basic auth). */
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

    /** Result of the last request() – for meaningful error messages. */
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
     * Send a recording to the manager and fetch the recognized text.
     * The manager forwards to the speech service (Parakeet); the format does not
     * matter, ffmpeg converts to 16-kHz mono there.
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

    /** Speak the text; returns the WAV data or null. */
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

    /** Result of a chat long-poll: `chats` is null when nothing has changed. */
    data class ChatPoll(val rev: Long, val chats: String?, val tombstones: String?)

    /**
     * Long-poll on the shared chat store: the manager only answers once someone
     * (app OR web) writes — or after `waitSec` seconds without content. This way
     * others' messages arrive here within fractions of a second, without constant
     * polling. Older managers without `since`/`wait` return the bare list; the
     * parser catches that and the caller falls back to waiting.
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

    fun push(baseUrl: String, user: String, pass: String, json: String): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/chats", user, pass, json) != null

    /** One step of a mission (multi-stage orchestrator job). */
    data class MissionStep(val n: Int, val text: String, val status: String, val taskId: String)

    /** A mission: plan + progress live in the manager. */
    data class Mission(val id: String, val goal: String, val status: String,
                       val steps: List<MissionStep>, val summary: String, val lastLog: String)

    fun listMissions(baseUrl: String, user: String, pass: String): List<Mission>? {
        val raw = request("GET", "${baseUrl.trimEnd('/')}/api/missions?instance=orchestrator",
            user, pass, null) ?: return null
        return try {
            val arr = JSONObject(raw).optJSONArray("missions") ?: return emptyList()
            (0 until arr.length()).map { i ->
                val m = arr.getJSONObject(i)
                val sa = m.optJSONArray("steps")
                val steps = if (sa == null) emptyList() else (0 until sa.length()).map { j ->
                    val st = sa.getJSONObject(j)
                    MissionStep(st.optInt("n"), st.optString("text"),
                        st.optString("status"), st.optString("task_id"))
                }
                val log = m.optJSONArray("log")
                Mission(m.optString("id"), m.optString("goal"), m.optString("status"),
                    steps, m.optString("summary"),
                    if (log != null && log.length() > 0) log.optString(log.length() - 1) else "")
            }
        } catch (e: Exception) { null }
    }

    /** pause | resume | abort a mission (admin). */
    fun missionAction(baseUrl: String, user: String, pass: String, id: String, action: String): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/mission-admin", user, pass,
            JSONObject().put("id", id).put("action", action).toString()) != null

    /** Prompt templates (slash commands) from the manager. */
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

    /** Feed a message into a RUNNING turn (steering). true = queued. */
    fun steer(baseUrl: String, user: String, pass: String, instance: String, message: String): Boolean {
        val raw = request("POST", "${baseUrl.trimEnd('/')}/i/$instance/api/steer", user, pass,
            JSONObject().put("message", message).toString()) ?: return false
        return try { JSONObject(raw).optBoolean("queued") } catch (e: Exception) { false }
    }

    /** A push notification from the manager. */
    data class NotifItem(val id: String, val ts: Long, val title: String,
                         val body: String, val instance: String, val read: Boolean,
                         val link: String = "")

    /** Result of the notification long-poll: `items` is null on timeout. */
    data class NotifPoll(val rev: Long, val items: List<NotifItem>?, val unread: Int)

    /** Long-poll on /api/notifications — analogous to pollChats. */
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

    /** Acknowledge all notifications as read. */
    fun markNotifRead(baseUrl: String, user: String, pass: String): Boolean =
        request("POST", "${baseUrl.trimEnd('/')}/api/notifications/read", user, pass, "{\"all\":true}") != null

    /** Security gateway: which chats are filtered and how much has been removed
     *  so far. The state lives in the manager, not on the device — otherwise it
     *  would differ between app and web. */
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

    /** Create + start a server agent (manager instance). Returns a status message. */
    fun createAndStart(
        baseUrl: String, user: String, pass: String,
        name: String, template: String, config: JSONObject,
    ): String {
        val base = baseUrl.trimEnd('/')
        val cBody = JSONObject().put("name", name).put("template", template).put("config", config).toString()
        val c = request("POST", "$base/api/create", user, pass, cBody)
            ?: return "⚠️ Creation failed: $lastStatus"
        val cMsg = try { JSONObject(c).optString("msg", c) } catch (e: Exception) { c }
        // The manager returns errors as HTTP 200 + {msg:"…failed/exists…"} too; match its tokens
        if (cMsg.contains("exists") || cMsg.contains("invalid") || cMsg.contains("unknown"))
            return "⚠️ $cMsg"
        val s = request("POST", "$base/api/instances/$name/start", user, pass, "")
            ?: return "$cMsg · ⚠️ Start failed: $lastStatus"
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
                    401 -> " (check username/password)"
                    404 -> " (endpoint/instance not found)"
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
