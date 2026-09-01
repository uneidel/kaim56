// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Recognising voice commands — and above all: NOT recognising them where a
// real question for the agent stands. The expensive mistake would be to
// swallow a question.
package de.kat56.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VoiceCommandTest {

    @Test
    fun `the bare command is recognised`() {
        assertEquals(VoiceCommand.Action.SCREENSHOT, VoiceCommand.parse("Screenshot")?.action)
        assertEquals(VoiceCommand.Action.PHOTO, VoiceCommand.parse("Foto")?.action)
        assertEquals(VoiceCommand.Action.STOP, VoiceCommand.parse("Stopp!")?.action)
    }

    @Test
    fun `politeness and verbs in front do not get in the way`() {
        val screenshots = listOf(
            "Please take a screenshot",
            "take a screenshot",
            "Can you please take a screenshot",
            "Bildschirmfoto",
        )
        for (s in screenshots) {
            assertEquals("not recognised: '$s'",
                VoiceCommand.Action.SCREENSHOT, VoiceCommand.parse(s)?.action)
        }
        for (s in listOf("nimm ein Foto", "Take a photo", "take a picture")) {
            assertEquals("not recognised: '$s'",
                VoiceCommand.Action.PHOTO, VoiceCommand.parse(s)?.action)
        }
    }

    @Test
    fun `the question spoken along is preserved`() {
        val p = VoiceCommand.parse("Screenshot, what does it say?")
        assertEquals(VoiceCommand.Action.SCREENSHOT, p?.action)
        assertEquals("what does it say", p?.rest)
    }

    @Test
    fun `fillers before the remaining question fall away`() {
        assertEquals("what is that", VoiceCommand.parse("Photo and what is that?")?.rest)
    }

    @Test
    fun `real questions for the agent are NOT intercepted`() {
        // This is the expensive failure: a question that gets stuck in the phone.
        for (s in listOf(
            "What was on yesterday's screenshot?",
            "Send me the photo from the hike",
            "Explain to me how a screenshot works",
            "What is the weather?",
            "Stoppuhr auf drei Minuten stellen",
        )) {
            assertNull("wrongly taken as a command: '$s'", VoiceCommand.parse(s))
        }
    }

    @Test
    fun `empty input or bare fillers are not a command`() {
        assertNull(VoiceCommand.parse(""))
        assertNull(VoiceCommand.parse("   "))
        assertNull(VoiceCommand.parse("bitte"))
        assertNull(VoiceCommand.parse("mach"))
    }
}
