// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RecoveryTest {
    @Test
    fun `empty and broken-off replies are recovered, aborted and complete ones not`() {
        assertTrue(ServerAgent.needsRecovery(""))
        assertTrue(ServerAgent.needsRecovery("  \n"))
        assertTrue(ServerAgent.needsRecovery("⟦think⟧half a thought\n⚠️ Fehler: Socket is closed"))
        assertTrue(ServerAgent.needsRecovery("⚠️ HTTP 502"))
        assertFalse(ServerAgent.needsRecovery("_(aborted)_"))
        assertFalse(ServerAgent.needsRecovery("⟦think⟧x⟦/think⟧OK"))
        assertFalse(ServerAgent.needsRecovery("⚠️ Fehler mentioned mid-text\nbut the reply went on"))
    }

    @Test
    fun `the trace decides - unknown, running, nothing kept, or the answer`() {
        assertEquals("unknown", ServerAgent.recovered(JSONObject("""{"turn":null,"llm":[]}""")).state)
        assertEquals("running", ServerAgent.recovered(JSONObject("""{"turn":{"ts_start":1,"ts_end":null}}""")).state)
        assertEquals("none", ServerAgent.recovered(JSONObject("""{"turn":{"ts_end":5},"answer":null}""")).state)
        assertEquals("none", ServerAgent.recovered(JSONObject("""{"turn":{"ts_end":5}}""")).state)   // old manager
        val r = ServerAgent.recovered(JSONObject("""{"turn":{"ts_end":5},"answer":"⟦think⟧x⟦/think⟧OK"}"""))
        assertEquals(ServerAgent.Recovered("answer", "⟦think⟧x⟦/think⟧OK"), r)
    }

    @Test
    fun `our turn ids are what the bridge accepts`() {
        repeat(20) { assertTrue(Regex("[0-9a-f]{8,16}").matches(ServerAgent.newTurnId())) }
    }
}
