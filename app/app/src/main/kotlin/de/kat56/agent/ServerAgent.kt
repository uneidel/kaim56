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
}
