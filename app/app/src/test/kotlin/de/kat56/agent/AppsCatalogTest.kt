// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AppsCatalogTest {
    private val apps = """{"apps":[
        {"name":"codeflow","title":"Code flow","icon":"🔀","served_by":"celld","cloud":{"where":"local","url":"https://kaim56-codeflow.kat56.workers.dev"}},
        {"name":"corewar","title":"Core War","served_by":"cloudflare","cloud":{"where":"cloud","url":"https://kaim56-corewar.kat56.workers.dev"}}]}"""
    private val cf = """{"workers":[
        {"name":"kaim56-corewar","app":"corewar","url":"https://kaim56-corewar.kat56.workers.dev"},
        {"name":"shipsinker","app":null,"url":"https://shipsinker.kat56.workers.dev","assets":true},
        {"name":"hidden","app":null,"url":""}]}"""

    @Test
    fun `local apps, moved apps and workers created elsewhere - each once`() {
        val e = AppsCatalog.parse(apps, cf)
        assertEquals(listOf("codeflow", "corewar", "shipsinker", "hidden"), e.map { it.name })
        assertEquals(listOf("local", "cloud", "cfonly", "cfonly"), e.map { it.where })
        assertEquals("", e[0].url)                                    // a local app opens through the manager
        assertEquals("https://kaim56-corewar.kat56.workers.dev", e[1].url)
        assertTrue(e[2].detail.contains("static files"))
    }

    @Test
    fun `no Cloudflare list or a broken answer still lists the local apps`() {
        assertEquals(2, AppsCatalog.parse(apps, null).size)
        assertEquals(2, AppsCatalog.parse(apps, "<html>").size)
        assertEquals(0, AppsCatalog.parse(null, null).size)
    }

    @Test
    fun `the fetch shim goes first into head, or in front without one`() {
        val h = AppsCatalog.inject("<!doctype html><HEAD lang=x><title>t</title></HEAD><body></body>")
        assertTrue(h.startsWith("<!doctype html><HEAD lang=x><script>"))
        assertTrue(h.indexOf("KaimBridge") < h.indexOf("<title>"))
        assertTrue(AppsCatalog.inject("<p>x</p>").startsWith("<script>"))
    }
}
