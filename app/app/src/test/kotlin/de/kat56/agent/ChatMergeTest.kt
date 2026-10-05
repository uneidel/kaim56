// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatMergeTest {
    private fun chat(id: String, at: Long, vararg texts: String, title: String = "T") =
        Conversation(id = id, title = title, mode = "server", updatedAt = at).also { c ->
            texts.forEachIndexed { i, t -> c.messages.add(Msg(i % 2 == 0, t)) }
        }
    private fun texts(c: Conversation) = c.messages.map { it.text }

    @Test
    fun `objects are filled, never replaced - a streaming reply keeps its list`() {
        val local = chat("a", 1, "q1", "r1")
        val held = local.messages                       // what send() streams into
        val r = ChatMerge.merge(listOf(local), listOf(chat("a", 2, "q1", "r1", "q2", "r2")), emptyMap())
        assertTrue(r.changed)
        assertSame(local, r.conversations.single())
        assertSame(held, local.messages)
        assertEquals(listOf("q1", "r1", "q2", "r2"), texts(local))
        assertEquals(2L, local.updatedAt)
    }

    @Test
    fun `a just-typed local question survives a newer remote that lacks it`() {
        val local = chat("a", 5, "q1", "r1", "unsent question")
        val r = ChatMerge.merge(listOf(local), listOf(chat("a", 9, "q1", "r1")), emptyMap())
        assertFalse(r.changed)
        assertEquals(listOf("q1", "r1", "unsent question"), texts(local))
    }

    @Test
    fun `real divergence adopts the newer, not shorter remote state`() {
        val local = chat("a", 1, "q1", "local answer")
        ChatMerge.merge(listOf(local), listOf(chat("a", 2, "q1", "server answer", "q2")), emptyMap())
        assertEquals(listOf("q1", "server answer", "q2"), texts(local))
    }

    @Test
    fun `local newer, busy chat and tombstones are left alone`() {
        val newer = chat("a", 9, "mine")
        val busy = chat("b", 1, "q")
        val r = ChatMerge.merge(listOf(newer, busy),
            listOf(chat("a", 5, "old"), chat("b", 2, "q", "partial from elsewhere"), chat("gone", 3, "x")),
            tombs = mapOf("gone" to 3L), busyId = "b")
        assertFalse(r.changed)
        assertEquals(listOf("mine"), texts(newer))
        assertEquals(listOf("q"), texts(busy))
        assertTrue(r.conversations.none { it.id == "gone" })
    }

    @Test
    fun `new remote chats are added, newest first, titles follow the remote`() {
        val local = chat("a", 1, "q", title = "Neuer Chat")
        val r = ChatMerge.merge(listOf(local), listOf(chat("a", 4, "q", "r", title = "Weather"), chat("b", 3, "hi")), emptyMap())
        assertTrue(r.changed)
        assertEquals(listOf("a", "b"), r.conversations.map { it.id })
        assertEquals("Weather", local.title)
        // deleted after the remote's last change -> stays deleted; a later change would resurrect it
        assertTrue(ChatMerge.merge(emptyList(), listOf(chat("c", 5, "x")), mapOf("c" to 5L)).conversations.isEmpty())
        assertEquals(1, ChatMerge.merge(emptyList(), listOf(chat("c", 6, "x")), mapOf("c" to 5L)).conversations.size)
    }
}
