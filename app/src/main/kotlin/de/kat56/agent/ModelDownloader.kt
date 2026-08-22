// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** Downloads a (possibly HuggingFace-gated) .task model — **resumable**.
 *
 *  - Downloaded into <target>.part; on interruption the partial file is kept and
 *    resumed on the next call via HTTP Range (not restarted from scratch).
 *  - A marker file <target>.part.url records which URL the partial belongs to;
 *    if the URL differs, the old partial is discarded.
 *  - HuggingFace resolve -> 302 to a signed CDN URL: send the Auth header only to
 *    huggingface.co, the Range header to both.
 */
object ModelDownloader {
    fun download(
        url: String,
        token: String,
        outFile: File,
        onProgress: (done: Long, total: Long) -> Unit,
    ): String {
        val part = File(outFile.absolutePath + ".part")
        val marker = File(outFile.absolutePath + ".part.url")

        // Does the existing partial belong to THIS URL? Otherwise start over.
        val resumable = part.exists() && marker.exists() && marker.readText() == url
        if (!resumable) {
            part.delete()
            marker.writeText(url)
        }
        var startAt = if (part.exists()) part.length() else 0L

        var current = url
        var redirects = 0
        while (redirects < 6) {
            val conn = URL(current).openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = false
            conn.connectTimeout = 20000
            conn.readTimeout = 60000
            if (redirects == 0 && token.isNotBlank()) {
                conn.setRequestProperty("Authorization", "Bearer $token")
            }
            if (startAt > 0) conn.setRequestProperty("Range", "bytes=$startAt-")

            val code = conn.responseCode
            if (code in 300..399) {
                val loc = conn.getHeaderField("Location") ?: throw IOException("Redirect without Location")
                conn.disconnect(); current = loc; redirects++; continue
            }

            val append: Boolean
            val total: Long
            when (code) {
                HttpURLConnection.HTTP_PARTIAL -> {           // 206 -> resume
                    append = true
                    val cr = conn.getHeaderField("Content-Range")   // bytes start-end/total
                    total = cr?.substringAfterLast('/')?.toLongOrNull()
                        ?: (startAt + conn.contentLengthLong)
                }
                HttpURLConnection.HTTP_OK -> {                // 200 -> server ignores Range: start fresh
                    append = false
                    startAt = 0
                    total = conn.contentLengthLong
                }
                else -> {
                    val err = conn.errorStream?.bufferedReader()?.use { it.readText() }?.take(300)
                    conn.disconnect()
                    throw IOException("HTTP $code: ${err ?: ""}")
                }
            }

            FileOutputStream(part, append).use { out ->
                conn.inputStream.use { inp ->
                    val buf = ByteArray(1 shl 16)
                    var done = startAt
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        onProgress(done, total)
                    }
                }
            }
            conn.disconnect()

            // IMPORTANT: only finalize if truly complete. Otherwise the .part file
            // is kept and resumed next time — prevents a broken (truncated)
            // model.task ("Unable to open zip archive").
            if (total > 0 && part.length() < total) {
                throw IOException("Download incomplete (${part.length()}/$total B) — restart to resume.")
            }

            if (outFile.exists()) outFile.delete()
            if (!part.renameTo(outFile)) throw IOException("Renaming the finished file failed")
            marker.delete()
            return outFile.absolutePath
        }
        throw IOException("Too many redirects")
    }
}
