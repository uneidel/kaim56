// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.json.JSONArray
import org.json.JSONObject

/** Something an agent cannot go on without you: an approval, a question or a review. */
data class WaitingItem(
    val id: String,
    val kind: String,          // approval | question | review
    val status: String,
    val created: String,
    val project: String,
    val squadId: String,
    val instance: String,
    val ticket: String,
    val title: String,
    val detail: String,
    val options: List<String>,
    val tool: String,
    val target: String,
) {
    /** The heading the screen groups by: project and squad, whatever is known. */
    val group: String get() = listOf(project, squadId).filter { it.isNotBlank() }.joinToString(" · ").ifBlank { "General" }
}

/**
 * The manager's "Waiting for you" queue (/api/waiting) behind two calls:
 * [open] lists what is waiting, [act] decides one item. The rules of what a
 * kind allows ([actionsFor], [check]) live here too, so the screen only draws.
 */
class WaitingClient(private val mc: ManagerClient) {
    constructor(prefs: Prefs) : this(ManagerClient(prefs))

    sealed class Outcome {
        data class Done(val item: WaitingItem?) : Outcome()
        /** HTTP 409: someone decided it elsewhere (web, another phone) — refresh. */
        data class AlreadyDecided(val msg: String) : Outcome()
        data class Failed(val msg: String) : Outcome()
    }

    /** Why the last [open] failed ("" after a success). */
    var lastError = ""
        private set

    /** The open items, or null when the manager could not be asked. */
    fun open(): List<WaitingItem>? {
        val (code, body) = call("GET", "/api/waiting?status=open", null)
        if (code !in 200..299) { lastError = errorOf(code, body); return null }
        lastError = ""
        return parse(body)
    }

    fun act(id: String, action: String, text: String = "", choice: String = ""): Outcome {
        val (code, body) = call("POST", "/api/waiting/${ManagerClient.enc(id)}", body(action, text, choice))
        return when {
            code == 409 -> Outcome.AlreadyDecided(errorOf(code, body))
            code !in 200..299 -> Outcome.Failed(errorOf(code, body))
            else -> {
                val o = try { JSONObject(body) } catch (e: Exception) { JSONObject() }
                if (o.optString("error").isNotBlank()) Outcome.Failed(o.optString("error"))
                else Outcome.Done(o.optJSONObject("item")?.let(::item))
            }
        }
    }

    /** (HTTP status, body) — the error body too, it carries the reason. Status -1 = no answer. */
    private fun call(method: String, path: String, body: String?): Pair<Int, String> = try {
        val conn = mc.connect(path, method)
        try {
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = conn.responseCode
            val s = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() } ?: ""
            code to s
        } finally { conn.disconnect() }
    } catch (e: Exception) { -1 to (e.message ?: e.toString()) }

    companion object {
        const val LINK = "waiting"
        const val TITLE_PREFIX = "Waiting for you"

        fun parse(json: String?): List<WaitingItem> = try {
            val arr = JSONObject(json ?: "").optJSONArray("items") ?: JSONArray()
            (0 until arr.length()).mapNotNull { arr.optJSONObject(it)?.let(::item) }.filter { it.id.isNotBlank() }
        } catch (e: Exception) { emptyList() }

        private fun item(o: JSONObject): WaitingItem {
            val opts = o.optJSONArray("options")
            return WaitingItem(
                id = o.optString("id"), kind = o.optString("kind"), status = o.optString("status", "open"),
                created = o.optString("created"), project = o.optString("project"),
                squadId = o.optString("squad_id"), instance = o.optString("instance"),
                ticket = o.optString("ticket"), title = o.optString("title"), detail = o.optString("detail"),
                options = if (opts == null) emptyList()
                          else (0 until opts.length()).map { opts.optString(it) }.filter { it.isNotBlank() },
                tool = o.optString("tool"), target = o.optString("target"),
            )
        }

        /** The request body of a decision; empty text/choice are left out. */
        fun body(action: String, text: String = "", choice: String = ""): String =
            JSONObject().put("action", action).apply {
                if (text.isNotBlank()) put("text", text.trim())
                if (choice.isNotBlank()) put("choice", choice)
            }.toString()

        /** The badge: how many still wait for a decision. */
        fun count(items: List<WaitingItem>): Int = items.count { it.status == "open" }

        /** The decisions a kind allows, in button order (an unknown kind gets none). */
        fun actionsFor(kind: String): List<String> = when (kind) {
            "approval" -> listOf("approve", "deny")
            "question" -> listOf("answer")
            "review" -> listOf("approve", "request_changes")
            else -> emptyList()
        }

        /** Why this decision cannot be sent yet, or null when it can. */
        fun check(kind: String, action: String, text: String, choice: String): String? = when {
            action !in actionsFor(kind) -> "Not possible for a $kind"
            kind == "review" && action == "request_changes" && text.isBlank() -> "Say what should change"
            kind == "question" && text.isBlank() && choice.isBlank() -> "Type or choose an answer"
            else -> null
        }

        /** Where a tapped notification leads: the manager's link, or the Waiting
         *  screen for "Waiting for you: …" notifications that carry none. The
         *  manager's squad links name web tabs the app does not have: squad
         *  decisions live on the Waiting screen here. */
        fun linkFor(title: String, link: String): String = when {
            link.startsWith(LINK) || link == "squads" || link == "projects" -> LINK
            link.isNotBlank() -> link
            title.startsWith(TITLE_PREFIX) -> LINK
            else -> ""
        }

        private fun errorOf(code: Int, body: String): String {
            val err = try { JSONObject(body).optString("error") } catch (e: Exception) { "" }
            return err.ifBlank { if (code < 0) body else "HTTP $code" }
        }
    }
}
