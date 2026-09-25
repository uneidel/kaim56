// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.junit.Assert.assertEquals
import org.junit.Test

class NotifSyncTest {
    private fun n(id: String, ts: Long, read: Boolean = false) =
        ManagerSync.NotifItem(id, ts, "t$id", "b", "inst", read)

    @Test
    fun `unread entries newer than the watermark are shown, oldest first`() {
        val items = listOf(n("c", 300), n("a", 100), n("b", 200), n("r", 400, read = true))
        assertEquals(listOf("b", "c"), NotifSync.pick(items, lastTs = 100, nowS = 1000).map { it.id })
    }

    @Test
    fun `first run shows only the last day`() {
        val now = 10 * 86400L
        val items = listOf(n("old", now - 2 * 86400), n("fresh", now - 3600), n("readNew", now - 60, read = true))
        assertEquals(listOf("fresh"), NotifSync.pick(items, lastTs = 0, nowS = now).map { it.id })
    }

    @Test
    fun `nothing new means nothing shown`() {
        assertEquals(0, NotifSync.pick(listOf(n("a", 50)), lastTs = 50, nowS = 100).size)
        assertEquals(0, NotifSync.pick(emptyList(), lastTs = 0, nowS = 100).size)
    }
}
