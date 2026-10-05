// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files

class ChatSessionTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val dir = Files.createTempDirectory("chatsession").toFile()
    private var remoteChats = "[]"
    private val mgr = FakeManager { method, uri, _ ->
        val path = uri.substringBefore("?")
        when {
            path == "/api/chats" && method == "GET" -> 200 to remoteChats
            path == "/api/chats" -> 200 to """{"msg":"1 chats saved"}"""
            path == "/i/helper/api/chat/stream" -> 200 to "⟦think⟧hm⟦/think⟧Hello from the agent"
            path == "/api/trace/helper" -> 200 to """{"turn":{"ts_end":5},"answer":"the recovered answer"}"""
            else -> 404 to "{}"
        }
    }
    private val prefs = Prefs(MemPrefs()).apply { serverUrl = mgr.url; user = "admin"; pass = "pw"; mode = "server"; instance = "helper" }

    private fun session() = ChatSession(prefs, ChatStore(dir), scope)
    private fun waitFor(what: String, cond: () -> Boolean) {
        val until = System.currentTimeMillis() + 5000
        while (!cond()) { if (System.currentTimeMillis() > until) throw AssertionError("timeout: $what"); Thread.sleep(20) }
    }

    @After fun down() { scope.cancel(); mgr.close(); dir.deleteRecursively() }

    @Test
    fun `a server turn streams into a new bubble with our turn id, then busy ends`() {
        val s = session()
        var done: Pair<Int, String>? = null
        assertTrue(s.send("hi", "hi") { i, t -> done = i to t })
        assertTrue(s.busy)
        waitFor("turn done") { done != null }
        val msgs = s.current.messages
        assertEquals(listOf("hi", "⟦think⟧hm⟦/think⟧Hello from the agent"), msgs.map { it.text })
        assertEquals(12, msgs[1].turn?.length)                       // ours, before a byte flowed
        assertFalse(s.busy)
        assertEquals(1, done!!.first)
        val stream = mgr.requests.first { it.second == "/i/helper/api/chat/stream" }
        assertEquals("hi", JSONObject(stream.third).getString("message"))
        assertEquals(msgs[1].turn, JSONObject(stream.third).getString("turn"))
    }

    @Test
    fun `slash commands the app handles never reach the agent`() {
        val s = session()
        assertFalse(s.send("/help", "/help"))
        assertTrue(s.current.messages.last().text.startsWith("App commands"))
        assertFalse(s.busy)
        assertTrue(mgr.requests.none { it.second.contains("/chat/stream") })
    }

    @Test
    fun `a broken reply is recovered from the manager by its turn id`() = runBlocking {
        val s = session()
        s.current.instance = "helper"
        s.current.messages.add(Msg(true, "q"))
        s.current.messages.add(Msg(false, "", turn = "abcdef123456"))
        s.recoverReplies()
        assertEquals("the recovered answer", s.current.messages[1].text)
    }

    @Test
    fun `deleting a chat leaves a tombstone that is pushed and kept on reload`() {
        val s = session()
        val keep = s.current
        s.newChat()
        val gone = s.current
        s.deleteChat(gone)
        assertEquals(keep.id, s.currentId)
        waitFor("push") { mgr.requests.any { it.first == "POST" && it.second == "/api/chats" } }
        val push = JSONObject(mgr.requests.last { it.second == "/api/chats" && it.first == "POST" }.third)
        assertTrue(push.getJSONObject("tombstones").has(gone.id))
        // the same chat coming back from the server stays deleted (older than the tombstone)
        remoteChats = JSONArray().put(JSONObject().put("id", gone.id).put("title", "x").put("mode", "server")
            .put("updatedAt", 1).put("messages", JSONArray().put(JSONObject().put("user", true).put("text", "old")))).toString()
        val again = session()
        again.sync()
        waitFor("sync") { again.online }
        assertTrue(again.conversations.none { it.id == gone.id })
    }

    @Test
    fun `incoming tombstones remove a chat and move off it`() {
        val s = session()
        s.newChat()
        val doomed = s.current
        s.applyTombstones(JSONObject().put(doomed.id, System.currentTimeMillis() + 1000).toString())
        assertTrue(s.conversations.none { it.id == doomed.id })
        assertTrue(s.currentId != doomed.id)
    }
}
