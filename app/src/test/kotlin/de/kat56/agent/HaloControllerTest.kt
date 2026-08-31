// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The whole path without glasses: connect, load modules, record, speech
// recognition, instance, reply on the display. Instead of Bluetooth there is
// HaloDryLink — the same fake the app uses to walk the integration dry.
package de.kat56.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HaloControllerTest {

    private fun controller(
        link: HaloDryLink = HaloDryLink(),
        heard: String? = "what is the weather",
        answer: String = "Sunny, 21 degrees.",
        status: MutableList<String> = mutableListOf(),
    ): Pair<HaloController, HaloDryLink> {
        val c = HaloController(
            luaSource = { name -> "-- $name\nreturn {}" },
            transcribe = { heard },
            ask = { answer },
            onStatus = { status.add(it) },
        )
        c.attach(link)
        return c to link
    }

    @Test
    fun `connecting uploads all modules in the right order`() {
        val (c, link) = controller()
        assertEquals(c.modules, link.uploaded)
        // katagent last: it needs the others at startup.
        assertEquals("katagent", link.uploaded.last())
    }

    @Test
    fun `after connecting the display is empty`() {
        val (_, link) = controller()
        assertTrue(link.display.isEmpty())
    }

    @Test
    fun `listening switches the microphone on and announces it`() {
        val (c, link) = controller()
        c.startListening()
        assertTrue("the microphone has to run", link.recording)
        assertEquals(listOf("… listening"), link.display)
    }

    @Test
    fun `the whole path ends with the reply on the display`() {
        val status = mutableListOf<String>()
        val (c, link) = controller(status = status)
        c.startListening()
        val answer = c.stopAndAsk(ByteArray(64))
        assertEquals("Sunny, 21 degrees.", answer)
        assertTrue("the microphone has to be off", !link.recording)
        assertEquals(listOf("Sunny, 21 degrees."), link.display)
        assertTrue("the question is shown in between",
            status.any { it.contains("what is the weather") })
    }

    @Test
    fun `when nothing was understood, nothing is asked`() {
        var asked = 0
        val link = HaloDryLink()
        val c = HaloController(
            luaSource = { "" },
            transcribe = { null },                 // speech recognition returns nothing
            ask = { asked++; "should not happen" },
        )
        c.attach(link)
        c.startListening()
        assertNull(c.stopAndAsk(ByteArray(8)))
        assertEquals("no question may go out", 0, asked)
        assertEquals(listOf("did not catch that"), link.display)
    }

    @Test
    fun `without a connection nothing happens instead of crashing`() {
        val c = HaloController({ "" }, { "x" }, { "y" })
        c.startListening()                         // no session -> quiet
        assertNull(c.stopAndAsk(ByteArray(4)))
        c.show("whatever")
    }

    // ---- Photo by voice command ------------------------------------------

    @Test
    fun `a spoken photo triggers the camera and sends the image along`() {
        val link = HaloDryLink()
        var withImage: Pair<String, Int>? = null
        val c = HaloController(
            luaSource = { "" },
            transcribe = { "Photo, what is that?" },
            ask = { "should not run as a plain question" },
            askWithImage = { q, jpeg -> withImage = q to jpeg.size; "That is a coffee mug." },
            awaitPhoto = { link.photo },
        )
        c.attach(link)
        val answer = c.stopAndAsk(ByteArray(16))
        assertEquals("That is a coffee mug.", answer)
        // The question spoken along goes to the instance with the image.
        assertEquals("what is that", withImage?.first)
        assertEquals(3, withImage?.second)
        assertEquals(listOf("That is a coffee mug."), link.display)
    }

    @Test
    fun `without a question spoken along the obvious one is asked`() {
        val link = HaloDryLink()
        var asked = ""
        val c = HaloController({ "" }, { "Photo" }, { "" },
            askWithImage = { q, _ -> asked = q; "a tree" }, awaitPhoto = { link.photo })
        c.attach(link)
        c.stopAndAsk(ByteArray(4))
        assertEquals("What do you see in this image?", asked)
    }

    @Test
    fun `if the image does not come, it is said instead of failing quietly`() {
        val link = HaloDryLink()
        var asked = 0
        val c = HaloController({ "" }, { "Photo" }, { "" },
            askWithImage = { _, _ -> asked++; "" }, awaitPhoto = { null })   // camera stays silent
        c.attach(link)
        assertNull(c.photoAndAsk())
        assertEquals(0, asked)
        assertEquals(listOf("no image received"), link.display)
    }

    @Test
    fun `a real question travels on to the instance, not into the camera`() {
        val link = HaloDryLink()
        var plain = ""
        val c = HaloController({ "" }, { "Send me the photo from yesterday" },
            ask = { plain = it; "here it is" },
            askWithImage = { _, _ -> "WRONG: camera triggered" },
            awaitPhoto = { link.photo })
        c.attach(link)
        assertEquals("here it is", c.stopAndAsk(ByteArray(4)))
        assertEquals("Send me the photo from yesterday", plain)
        assertNull("the camera must not have been triggered", link.photo)
    }

    @Test
    fun `cancelling stops the recording and wipes the display`() {
        val link = HaloDryLink()
        val c = HaloController({ "" }, { "Stop" }, { "should not be asked" })
        c.attach(link)
        c.startListening()
        assertNull(c.stopAndAsk(ByteArray(4)))
        assertTrue(!link.recording)
        assertTrue(link.display.isEmpty())
    }

    // ---- Line wrapping ---------------------------------------------------

    @Test
    fun `long replies are wrapped to the display width`() {
        val (c, _) = controller()
        val wrapped = c.wrap("The agent answers here with a longer sentence " +
            "that does not fit one line.", width = 20)
        wrapped.split("\n").forEach {
            assertTrue("too long: '$it'", it.length <= 20)
        }
        // No word may be lost in the process.
        assertEquals("The agent answers here with a longer sentence that does not fit one line.",
            wrapped.replace("\n", " "))
    }

    @Test
    fun `overlong words are broken hard instead of running off the screen`() {
        val (c, _) = controller()
        val wrapped = c.wrap("See https://agents.example.com/a/very/long/path/to/file.txt", width = 16)
        wrapped.split("\n").forEach { assertTrue("too long: '$it'", it.length <= 16) }
        assertTrue(wrapped.replace("\n", "").contains("file.txt"))
    }

    @Test
    fun `existing line breaks are preserved`() {
        val (c, _) = controller()
        assertEquals("one\ntwo\nthree", c.wrap("one\ntwo\nthree", width = 20))
    }

    @Test
    fun `the display is cut to the height of the screen`() {
        val (c, link) = controller()
        c.show((1..20).joinToString("\n") { "line $it" }, maxLines = 8)
        assertEquals(8, link.display.size)
        assertEquals("line 1", link.display.first())
    }
}
