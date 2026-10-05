// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.content.SharedPreferences
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread

/** A minimal HTTP/1.1 manager for tests (java.net only; one request per
 *  connection). [route] gets (method, path?query, body) -> (status, body). */
class FakeManager(private val route: (String, String, String) -> Pair<Int, String>) : AutoCloseable {
    private val srv = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    val url get() = "http://127.0.0.1:${srv.localPort}"
    val requests: MutableList<Triple<String, String, String>> = java.util.Collections.synchronizedList(mutableListOf())

    init {
        thread(isDaemon = true) {
            while (!srv.isClosed) {
                val sock = try { srv.accept() } catch (e: Exception) { break }
                thread(isDaemon = true) {
                    sock.use { so ->
                        val inp = so.getInputStream().bufferedReader(Charsets.UTF_8)
                        val (method, uri) = inp.readLine().split(" ").let { it[0] to it[1] }
                        val headers = generateSequence { inp.readLine()?.takeIf { it.isNotEmpty() } }
                            .associate { it.substringBefore(":").lowercase() to it.substringAfter(":").trim() }
                        val len = headers["content-length"]?.toInt() ?: 0
                        val body = CharArray(len).also { var n = 0; while (n < len) { val r = inp.read(it, n, len - n); if (r < 0) break; n += r } }
                        requests.add(Triple(method, uri, String(body)))
                        val (code, out) = route(method, uri, String(body))
                        val b = out.toByteArray()
                        so.getOutputStream().apply {
                            write("HTTP/1.1 $code X\r\nContent-Type: application/json\r\nContent-Length: ${b.size}\r\nConnection: close\r\n\r\n".toByteArray())
                            write(b); flush()
                        }
                    }
                }
            }
        }
    }
    override fun close() = srv.close()
}

/** SharedPreferences in memory — Prefs for JVM tests. */
class MemPrefs : SharedPreferences {
    private val m = java.util.concurrent.ConcurrentHashMap<String, Any>()
    override fun getAll(): MutableMap<String, *> = m
    override fun getString(k: String?, d: String?) = m[k] as? String ?: d
    override fun getStringSet(k: String?, d: MutableSet<String>?) = @Suppress("UNCHECKED_CAST") (m[k] as? MutableSet<String> ?: d)
    override fun getInt(k: String?, d: Int) = m[k] as? Int ?: d
    override fun getLong(k: String?, d: Long) = m[k] as? Long ?: d
    override fun getFloat(k: String?, d: Float) = m[k] as? Float ?: d
    override fun getBoolean(k: String?, d: Boolean) = m[k] as? Boolean ?: d
    override fun contains(k: String?) = m.containsKey(k)
    override fun registerOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
    override fun unregisterOnSharedPreferenceChangeListener(l: SharedPreferences.OnSharedPreferenceChangeListener?) {}
    override fun edit(): SharedPreferences.Editor = object : SharedPreferences.Editor {
        override fun putString(k: String?, v: String?) = apply { if (v == null) m.remove(k!!) else m[k!!] = v }
        override fun putStringSet(k: String?, v: MutableSet<String>?) = apply { if (v == null) m.remove(k!!) else m[k!!] = v }
        override fun putInt(k: String?, v: Int) = apply { m[k!!] = v }
        override fun putLong(k: String?, v: Long) = apply { m[k!!] = v }
        override fun putFloat(k: String?, v: Float) = apply { m[k!!] = v }
        override fun putBoolean(k: String?, v: Boolean) = apply { m[k!!] = v }
        override fun remove(k: String?) = apply { m.remove(k!!) }
        override fun clear() = apply { m.clear() }
        override fun commit() = true
        override fun apply() {}
    }
}
