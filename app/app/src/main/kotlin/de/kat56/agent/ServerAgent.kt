// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.util.Base64
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL

/** Server mode: chats with the running agent via the manager proxy
 *  ({base}/i/{instance}/api/chat, body {"message":..}, response {"reply":..}). */
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
                // chat: only for the security gateway in the manager — the guest
                // never sees the field, it is stripped out there.
                val p = JSONObject().put("message", message)
                if (chatId.isNotEmpty()) p.put("chat", chatId)
                it.write(p.toString().toByteArray())
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) return "⚠️ HTTP $code: ${body.take(300)}"
            try {
                JSONObject(body).optString("reply", body)
            } catch (e: Exception) {
                body
            }
        } catch (e: Exception) {
            "⚠️ Error: ${e.message}"
        } finally {
            conn.disconnect()
        }
    }

    /** Streaming: POST /i/{instance}/api/chat/stream -> tokens as raw text.
     *  onPartial is called per chunk. Return: null=ok, otherwise error text. */
    /** Cancel handle: cancel() drops the running connection -> the blocking
     *  read() aborts immediately, no matter how long the model is thinking. */
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
        onPartial: (String) -> Unit,
    ): String? {
        val url = URL("${baseUrl.trimEnd('/')}/i/$instance/api/chat/stream")
        val conn = url.openConnection() as HttpURLConnection
        cancel?.disconnect = { runCatching { conn.disconnect() } }   // cancel = drop the connection
        return try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15000
            conn.readTimeout = 600000      // tolerate long model pauses; cancellation goes
            // through cancel.disconnect(), NOT through a short read timeout
            // (a timeout closes the socket -> "Socket is closed").
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            if (user.isNotEmpty()) {
                val cred = Base64.encodeToString("$user:$pass".toByteArray(), Base64.NO_WRAP)
                conn.setRequestProperty("Authorization", "Basic $cred")
            }
            val payload = JSONObject().put("message", message)
            if (image != null) payload.put("image", image)   // Base64 JPEG (without data: prefix)
            if (chatId.isNotEmpty()) payload.put("chat", chatId)
            conn.outputStream.use { it.write(payload.toString().toByteArray()) }
            val code = conn.responseCode
            val stream = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?: return "⚠️ HTTP $code"
            val reader = stream.bufferedReader()
            val buf = CharArray(256)
            while (true) {
                val n = reader.read(buf)     // blocks; cancel.disconnect() aborts it
                if (n < 0) break
                if (n > 0) onPartial(String(buf, 0, n))
            }
            if (code !in 200..299) "⚠️ HTTP $code" else null
        } catch (e: Exception) {
            "⚠️ Error: ${e.message}"
        } finally {
            cancel?.disconnect = null
            conn.disconnect()
        }
    }
}
