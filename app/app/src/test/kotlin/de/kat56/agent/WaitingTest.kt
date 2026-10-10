// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WaitingTest {
    private val list = """{"items":[
        {"id":"w1","kind":"approval","status":"open","created":"2026-10-10T08:00:00","project":"shop","squad_id":"s1",
         "instance":"dev1","ticket":"T-7","title":"Run rm -rf build","detail":"cleanup","tool":"shell","target":"/build"},
        {"id":"w2","kind":"question","status":"open","title":"Which DB?","options":["postgres","sqlite",""]},
        {"kind":"review","title":"no id - dropped"},
        {"id":"w3","kind":"review","status":"decided","title":"PR 12"}],"count":3}"""

    private fun client(f: FakeManager) = WaitingClient(ManagerClient(f.url, "admin", "pw"))

    @Test
    fun `parsing is robust to missing fields`() {
        val items = WaitingClient.parse(list)
        assertEquals(listOf("w1", "w2", "w3"), items.map { it.id })
        val a = items[0]
        assertEquals("approval", a.kind); assertEquals("T-7", a.ticket); assertEquals("shell", a.tool)
        assertEquals("shop · s1", a.group)
        assertEquals(listOf("postgres", "sqlite"), items[1].options)
        assertEquals("", items[1].instance); assertEquals("General", items[1].group)
        assertEquals(emptyList<WaitingItem>(), WaitingClient.parse("not json"))
        assertEquals(emptyList<WaitingItem>(), WaitingClient.parse(null))
        assertEquals(emptyList<WaitingItem>(), WaitingClient.parse("{}"))
    }

    @Test
    fun `badge counts only open items`() {
        assertEquals(2, WaitingClient.count(WaitingClient.parse(list)))
        assertEquals(0, WaitingClient.count(emptyList()))
    }

    @Test
    fun `open asks for open items with auth`() {
        FakeManager { _, _, _ -> 200 to list }.use { f ->
            assertEquals(3, client(f).open()?.size)
            assertEquals("GET" to "/api/waiting?status=open", f.requests[0].let { it.first to it.second })
        }
        FakeManager { _, _, _ -> 500 to """{"error":"boom"}""" }.use { f ->
            val c = client(f)
            assertNull(c.open()); assertEquals("boom", c.lastError)
        }
    }

    @Test
    fun `each action sends its body`() {
        FakeManager { _, _, _ -> 200 to """{"ok":true,"item":{"id":"w1","kind":"approval","status":"approved"}}""" }.use { f ->
            val c = client(f)
            val r = c.act("w1", "approve", text = "  ok go ")
            assertEquals("approved", (r as WaitingClient.Outcome.Done).item?.status)
            c.act("w1", "deny")
            c.act("w 2", "answer", text = "", choice = "sqlite")
            c.act("w3", "request_changes", text = "add tests")
            val bodies = f.requests.map { JSONObject(it.third) }
            assertEquals("/api/waiting/w1", f.requests[0].second); assertEquals("POST", f.requests[0].first)
            assertEquals("approve", bodies[0].getString("action")); assertEquals("ok go", bodies[0].getString("text"))
            assertEquals("deny", bodies[1].getString("action")); assertTrue(!bodies[1].has("text") && !bodies[1].has("choice"))
            assertEquals("/api/waiting/w%202", f.requests[2].second)
            assertEquals("sqlite", bodies[2].getString("choice")); assertTrue(!bodies[2].has("text"))
            assertEquals("request_changes", bodies[3].getString("action")); assertEquals("add tests", bodies[3].getString("text"))
        }
    }

    @Test
    fun `409 is already decided, other errors carry the reason`() {
        FakeManager { _, p, _ -> if (p.endsWith("/gone")) 404 to """{"error":"not found"}""" else 409 to """{"error":"already approved"}""" }.use { f ->
            assertEquals(WaitingClient.Outcome.AlreadyDecided("already approved"), client(f).act("w1", "approve"))
            assertEquals(WaitingClient.Outcome.Failed("not found"), client(f).act("gone", "deny"))
        }
        FakeManager { _, _, _ -> 400 to "" }.use { f ->
            assertEquals(WaitingClient.Outcome.Failed("HTTP 400"), client(f).act("w1", "approve"))
        }
    }

    @Test
    fun `rules per kind`() {
        assertEquals(listOf("approve", "deny"), WaitingClient.actionsFor("approval"))
        assertEquals(listOf("approve", "request_changes"), WaitingClient.actionsFor("review"))
        assertEquals(emptyList<String>(), WaitingClient.actionsFor("weird"))
        assertNull(WaitingClient.check("approval", "deny", "", ""))
        assertNotNull(WaitingClient.check("approval", "answer", "x", ""))
        assertNotNull(WaitingClient.check("review", "request_changes", " ", ""))
        assertNull(WaitingClient.check("review", "approve", "", ""))
        assertNotNull(WaitingClient.check("question", "answer", "", ""))
        assertNull(WaitingClient.check("question", "answer", "", "sqlite"))
    }

    @Test
    fun `waiting notifications lead to the waiting screen`() {
        assertEquals("waiting", WaitingClient.linkFor("Waiting for you: Run rm", ""))
        assertEquals("waiting", WaitingClient.linkFor("x", "waiting:w1"))
        assertEquals("chat:dev1", WaitingClient.linkFor("Task done", "chat:dev1"))
        assertEquals("", WaitingClient.linkFor("Task done", ""))
    }
}
