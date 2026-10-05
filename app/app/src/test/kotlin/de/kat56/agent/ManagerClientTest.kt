// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread

class ManagerClientTest {
    private lateinit var srv: ServerSocket
    private val seen = java.util.Collections.synchronizedList(mutableListOf<String>())   // "METHOD path?query auth"
    @Volatile private var createMsg = "instance 'x' created from template 'openrouter'"
    private val base get() = "http://127.0.0.1:${srv.localPort}/"

    // a minimal HTTP/1.1 server (one request per connection) — java.net only
    @Before fun up() {
        srv = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true) {
            while (!srv.isClosed) {
                val sock = try { srv.accept() } catch (e: Exception) { break }
                sock.use { so ->
                    val inp = so.getInputStream().bufferedReader(Charsets.ISO_8859_1)
                    val (method, uri) = inp.readLine().split(" ").let { it[0] to it[1] }
                    val headers = generateSequence { inp.readLine()?.takeIf { it.isNotEmpty() } }
                        .associate { it.substringBefore(":").lowercase() to it.substringAfter(":").trim() }
                    val len = headers["content-length"]?.toInt() ?: 0
                    repeat(len) { inp.read() }
                    val auth = headers["authorization"] ?: ""
                    seen.add("$method $uri $auth")
                    val path = uri.substringBefore("?")
                    val (code, body) = when {
                        auth != "Basic " + java.util.Base64.getEncoder().encodeToString("admin:pw".toByteArray()) -> 401 to ""
                        path == "/api/tasks" -> 200 to """[{"id":"t1","status":"done"}]"""
                        path == "/api/extract" -> 200 to """{"name":"a b.pdf","text":"hello"}"""
                        path == "/api/create" -> 200 to JSONObject().put("msg", createMsg).toString()
                        path.endsWith("/start") -> 200 to """{"msg":"started (pid 7)"}"""
                        else -> 404 to "{}"
                    }
                    val b = body.toByteArray()
                    so.getOutputStream().apply {
                        write("HTTP/1.1 $code X\r\nContent-Type: application/json\r\nContent-Length: ${b.size}\r\nConnection: close\r\n\r\n".toByteArray())
                        write(b); flush()
                    }
                }
            }
        }
    }
    @After fun down() = srv.close()

    @Test
    fun `each client keeps its own error - a failed call does not touch another`() {
        val bad = ManagerClient(base, "admin", "wrong")
        val good = ManagerClient(base, "admin", "pw")
        assertNull(bad.listTasks())
        assertEquals(1, ManagerClient.parseTasks(good.listTasks()).size)
        assertTrue(bad.lastError.startsWith("HTTP 401"))
        assertEquals("", good.lastError)
    }

    @Test
    fun `document extraction reaches the manager (the URL was a literal template)`() {
        val r = ManagerClient(base, "admin", "pw").extract("a b.pdf", "x".toByteArray())
        assertEquals("hello", r?.text)
        assertTrue(seen.any { it.startsWith("POST /api/extract?name=a+b.pdf") })
    }

    @Test
    fun `a create the manager refused does not go on to start`() {
        val c = ManagerClient(base, "admin", "pw")
        createMsg = "'x' already exists"
        assertEquals("⚠️ 'x' already exists", c.createAndStart("x", "openrouter", JSONObject()))
        assertFalse(seen.any { it.contains("/start") })
        createMsg = "instance 'x' created from template 'openrouter'"
        assertTrue(c.createAndStart("x", "openrouter", JSONObject()).endsWith("started (pid 7)"))
    }

    @Test
    fun msgFailed() {
        for (m in listOf("'x' already exists", "invalid name", "unknown template 'y'", "error: boom")) assertTrue(m, ManagerClient.msgFailed(m))
        for (m in listOf("started (pid 7)", "instance 'x' created from template 'openrouter'")) assertFalse(m, ManagerClient.msgFailed(m))
    }
}
