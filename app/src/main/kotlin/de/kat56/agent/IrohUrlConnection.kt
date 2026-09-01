// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// A minimal HttpURLConnection that speaks HTTP/1.1 over one iroh bi stream to
// the manager gateway. Because the request carries `Connection: close`, the
// response body is read until end-of-stream — which is exactly what token
// streaming (/api/chat/stream) and the long-poll (/api/chats?wait=) need. This
// lets the app's existing HttpURLConnection call sites work unchanged; only the
// base URL changes to `iroh://<manager-node-id>`.
package de.kat56.agent

import android.content.Context
import uniffi.kaim_iroh.IrohStream
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL

class IrohUrlConnection(url: URL, private val ctx: Context) : HttpURLConnection(url) {
    private val reqHeaders = LinkedHashMap<String, String>()
    private var reqBody: ByteArrayOutputStream? = null
    private var stream: IrohStream? = null
    private var status = -1
    private val respHeaders = LinkedHashMap<String, String>()
    private var bodyIn: InputStream? = null
    @Volatile private var sent = false

    override fun setRequestProperty(key: String, value: String) { reqHeaders[key] = value }
    override fun addRequestProperty(key: String, value: String) { reqHeaders[key] = value }
    override fun getRequestProperty(key: String): String? = reqHeaders[key]

    override fun getOutputStream(): OutputStream {
        doOutput = true
        return reqBody ?: ByteArrayOutputStream().also { reqBody = it }
    }

    override fun connect() { /* lazy — the request is sent on first read */ }
    override fun usingProxy() = false
    override fun disconnect() { runCatching { stream?.close() } }

    private fun ensureSent() {
        if (sent) return
        sent = true
        val nodeId = url.host                        // iroh://<nodeid>/path
        val path = url.file.ifEmpty { "/" }          // "/api/…?query"
        val body = reqBody?.toByteArray()
        IrohNet.crumb(ctx, "req: open $requestMethod $path -> ${nodeId.take(12)}…")
        val st = IrohNet.client(ctx).open(nodeId)
        stream = st
        IrohNet.crumb(ctx, "req: stream opened; writing ${(body?.size ?: 0)}B body")
        val head = StringBuilder()
        head.append("$requestMethod $path HTTP/1.1\r\n")
        head.append("Host: kaim\r\n")
        for ((k, v) in reqHeaders) head.append("$k: $v\r\n")
        if (body != null) head.append("Content-Length: ${body.size}\r\n")
        head.append("Connection: close\r\n\r\n")
        st.write(head.toString().toByteArray(Charsets.ISO_8859_1))
        if (body != null && body.isNotEmpty()) st.write(body)
        st.finishSend()

        // Read up to the header terminator (\r\n\r\n), keep the rest as body head.
        val acc = ByteArrayOutputStream()
        var leftover = ByteArray(0)
        while (true) {
            val chunk = st.read(8192u)
            if (chunk.isEmpty()) break
            acc.write(chunk)
            val bytes = acc.toByteArray()
            val idx = indexOfHeaderEnd(bytes)
            if (idx >= 0) {
                parseHead(String(bytes, 0, idx, Charsets.ISO_8859_1))
                leftover = bytes.copyOfRange(idx + 4, bytes.size)
                break
            }
        }
        IrohNet.crumb(ctx, "req: response head parsed, status=$status")
        bodyIn = IrohBodyStream(st, leftover)
    }

    private fun parseHead(head: String) {
        val lines = head.split("\r\n")
        val statusLine = lines.firstOrNull().orEmpty()
        status = Regex("""HTTP/\d\.\d\s+(\d{3})""").find(statusLine)?.groupValues?.get(1)?.toIntOrNull() ?: -1
        for (i in 1 until lines.size) {
            val c = lines[i].indexOf(':')
            if (c > 0) respHeaders[lines[i].substring(0, c).trim().lowercase()] = lines[i].substring(c + 1).trim()
        }
    }

    override fun getResponseCode(): Int { ensureSent(); return status }
    override fun getInputStream(): InputStream { ensureSent(); return bodyIn ?: ByteArrayInputStream(ByteArray(0)) }
    override fun getErrorStream(): InputStream? { runCatching { ensureSent() }; return bodyIn }
    override fun getHeaderField(name: String): String? { ensureSent(); return respHeaders[name.lowercase()] }
    override fun getContentType(): String? = getHeaderField("content-type")

    private fun indexOfHeaderEnd(b: ByteArray): Int {
        var i = 0
        while (i + 3 < b.size) {
            if (b[i] == 13.toByte() && b[i + 1] == 10.toByte() &&
                b[i + 2] == 13.toByte() && b[i + 3] == 10.toByte()) return i
            i++
        }
        return -1
    }
}

/** Response body: the bytes already read past the header, then the stream to EOF. */
private class IrohBodyStream(private val st: IrohStream, prefix: ByteArray) : InputStream() {
    private var buf = prefix
    private var pos = 0
    private var eof = false

    private fun fill(): Boolean {
        while (pos >= buf.size) {
            if (eof) return false
            val c = try { st.read(16384u) } catch (e: Exception) { eof = true; return false }
            if (c.isEmpty()) { eof = true; return false }
            buf = c; pos = 0
        }
        return true
    }

    override fun read(): Int {
        if (!fill()) return -1
        return buf[pos++].toInt() and 0xff
    }

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (len == 0) return 0
        if (!fill()) return -1
        val n = minOf(len, buf.size - pos)
        System.arraycopy(buf, pos, b, off, n)
        pos += n
        return n
    }

    override fun close() { runCatching { st.close() } }
}
