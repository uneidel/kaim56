// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.util.Base64
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/** Server-Modus: chattet mit dem laufenden Agenten ueber den Manager-Proxy
 *  ({base}/i/{instance}/api/chat, Body {"message":..}, Antwort {"reply":..}). */
object ServerAgent {
    fun chat(
        baseUrl: String,
        instance: String,
        user: String,
        pass: String,
        message: String,
        chatId: String = "",
    ): String {
        val url = URL("${baseUrl.trimEnd('/')}/i/$instance/api/chat")
        val conn = url.openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15000
            conn.readTimeout = 300000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            if (user.isNotEmpty()) {
                val cred = Base64.encodeToString("$user:$pass".toByteArray(), Base64.NO_WRAP)
                conn.setRequestProperty("Authorization", "Basic $cred")
            }
            conn.outputStream.use {
                // chat: nur fuer das Security Gateway im Manager — der Gast
                // sieht das Feld nie, es wird dort herausgenommen.
                val p = JSONObject().put("message", message)
                if (chatId.isNotEmpty()) p.put("chat", chatId)
                it.write(p.toString().toByteArray())
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) return "⚠️ HTTP $code: ${plainText(body)}"
            try {
                JSONObject(body).optString("reply", body)
            } catch (e: Exception) {
                body
            }
        } catch (e: Exception) {
            "⚠️ Fehler: ${errText(e)}"
        } finally {
            conn.disconnect()
        }
    }

    /** Streaming: POST /i/{instance}/api/chat/stream -> Tokens als roher Text.
     *  onPartial wird je Chunk gerufen. Rueckgabe: null=ok, sonst Fehlertext. */
    /** Abbruch-Handle: cancel() trennt die laufende Verbindung -> der blockierende
     *  read() bricht sofort ab, egal wie lange das Modell gerade denkt. */
    class CancelHandle {
        @Volatile var disconnect: (() -> Unit)? = null
        fun cancel() { runCatching { disconnect?.invoke() } }
    }

    fun chatStream(
        baseUrl: String,
        instance: String,
        user: String,
        pass: String,
        message: String,
        image: String? = null,
        chatId: String = "",
        turn: String? = null,               // our own turn id: known before a byte flows (recovery)
        cancel: CancelHandle? = null,
        onTurn: (String) -> Unit = {},      // turn id from the X-Kaim-Turn header (trace)
        onPartial: (String) -> Unit,
    ): String? {
        val url = URL("${baseUrl.trimEnd('/')}/i/$instance/api/chat/stream")
        val conn = url.openConnection() as HttpURLConnection
        cancel?.disconnect = { runCatching { conn.disconnect() } }   // Abbruch = Verbindung trennen
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15000
            conn.readTimeout = 600000      // lange Modell-Pausen tolerieren; Abbruch laeuft
            // ueber cancel.disconnect(), NICHT ueber ein kurzes Read-Timeout
            // (ein Timeout schliesst den Socket -> "Socket is closed").
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            if (user.isNotEmpty()) {
                val cred = Base64.encodeToString("$user:$pass".toByteArray(), Base64.NO_WRAP)
                conn.setRequestProperty("Authorization", "Basic $cred")
            }
            val payload = JSONObject().put("message", message)
            if (image != null) payload.put("image", image)   // Base64 JPEG (ohne data:-Präfix)
            if (chatId.isNotEmpty()) payload.put("chat", chatId)
            if (!turn.isNullOrEmpty()) payload.put("turn", turn)
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }
            val code = conn.responseCode
            if (code !in 200..299) {
                // Fehlerkoerper NIE in die Blase streamen: Manager/Traefik antworten
                // mit HTML, das der Chat sonst als rohen <p>-Text zeigen wuerde.
                val body = conn.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val txt = plainText(body)
                return if (txt.isEmpty()) "⚠️ HTTP $code" else "⚠️ HTTP $code: $txt"
            }
            conn.getHeaderField("X-Kaim-Turn")?.takeIf { it.isNotBlank() }?.let(onTurn)
            val stream = conn.inputStream ?: return "⚠️ HTTP $code"
            val reader = stream.bufferedReader()
            val buf = CharArray(256)
            while (true) {
                val n = reader.read(buf)     // blockiert; cancel.disconnect() bricht es ab
                if (n < 0) break
                if (n > 0) onPartial(String(buf, 0, n))
            }
            null
        } catch (e: Exception) {
            "⚠️ Fehler: ${errText(e)}"
        } finally {
            cancel?.disconnect = null
            conn.disconnect()
        }
    }

    /** Fehlerseiten (HTML) in lesbaren Text: Skripte/Tags raus, Entities aufloesen,
     *  Whitespace normieren. Damit steht in der Blase "Instance 'x' is not running."
     *  statt "<p>Instance 'x' is not running.</p>". */
    fun plainText(raw: String): String {
        var s = raw.replace(Regex("(?is)<(script|style)[^>]*>.*?</\\1>"), " ")
        s = s.replace(Regex("(?is)<br\\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>"), " ")
        s = s.replace(Regex("(?s)<[^>]*>"), "")
        s = s.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", "\"").replace("&#39;", "'").replace("&amp;", "&")
        return s.replace(Regex("\\s+"), " ").trim().take(300)
    }

    /** Manche IOExceptions haben keine message — dann bleibt sonst "Fehler:" stehen. */
    private fun errText(e: Exception): String =
        e.message?.takeIf { it.isNotBlank() } ?: e.javaClass.simpleName

    /** Does this reply bubble need recovering? Empty, or it ended in a transport
     *  error the app appended ("⚠️ Fehler…", "⚠️ HTTP…"). An aborted reply does not. */
    fun needsRecovery(text: String): Boolean {
        if (text.isBlank()) return true
        val last = text.lines().lastOrNull { it.isNotBlank() }?.trim() ?: return true
        return last.startsWith("⚠️ Fehler") || last.startsWith("⚠️ HTTP")
    }

    /** What /api/trace/<instance>?turn= says about a lost reply: "unknown" (no such
     *  turn — old agent, claude bridge), "running" (ask again later), "none" (ended,
     *  nothing kept) or "answer" with the text the stream should have delivered. */
    data class Recovered(val state: String, val answer: String = "")

    fun recovered(tr: org.json.JSONObject): Recovered {
        val row = tr.optJSONObject("turn") ?: return Recovered("unknown")
        if (row.isNull("ts_end")) return Recovered("running")
        val ans = if (tr.isNull("answer")) "" else tr.optString("answer", "")
        return if (ans.isBlank()) Recovered("none") else Recovered("answer", ans)
    }

    /** A turn id for a new reply: 12 hex characters (the bridge accepts 8-16 hex). */
    fun newTurnId(): String = java.util.UUID.randomUUID().toString().replace("-", "").take(12)
}
