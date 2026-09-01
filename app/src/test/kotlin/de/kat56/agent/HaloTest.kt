// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Tests for the glasses protocol layer. They run on the JVM, without a device
// and without an emulator: `gradle testDebugUnitTest`. What is checked is
// exactly the part that is checkable without a Halo on the desk — framing,
// reassembly, audio flags, WAV header. Only the Bluetooth substrate is left
// untested.
package de.kat56.agent

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HaloTest {

    /** A fake instead of Bluetooth: remembers what was written. */
    private class FakeLink(override val mtu: Int = 247,
                           override val isHalo: Boolean = true) : HaloLink {
        val packets = mutableListOf<ByteArray>()
        val strings = mutableListOf<String>()
        override fun write(packet: ByteArray) { packets.add(packet) }
        override fun writeString(text: String) { strings.add(text) }
    }

    // ---- Sizes -----------------------------------------------------------

    @Test
    fun `Halo reports more MTU than it tolerates, hence two bytes off`() {
        assertEquals(243, Halo.maxDataLength(247, isHalo = false))
        assertEquals(241, Halo.maxDataLength(247, isHalo = true))
        assertEquals(244, Halo.maxStringLength(247, isHalo = false))
        assertEquals(242, Halo.maxStringLength(247, isHalo = true))
    }

    // ---- Framing ---------------------------------------------------------

    @Test
    fun `a short message fits into one packet with a length header`() {
        val payload = "hallo".toByteArray()
        val p = Halo.packets(0x0a, payload, maxDataLength = 64)
        assertEquals(1, p.size)
        assertEquals(Halo.DATA_FLAG, p[0][0])
        assertEquals(0x0a.toByte(), p[0][1])
        assertEquals(0, p[0][2].toInt())              // length hi
        assertEquals(5, p[0][3].toInt())              // length lo
        assertArrayEquals(payload, p[0].copyOfRange(4, p[0].size))
    }

    @Test
    fun `an empty payload yields one packet of length zero`() {
        val p = Halo.packets(0x0b, ByteArray(0), maxDataLength = 64)
        assertEquals(1, p.size)
        assertEquals(4, p[0].size)
        assertEquals(0, p[0][2].toInt())
        assertEquals(0, p[0][3].toInt())
    }

    @Test
    fun `a long message is split and comes back complete`() {
        val payload = ByteArray(5000) { (it % 251).toByte() }
        val maxData = 241
        val p = Halo.packets(0x0a, payload, maxData)
        assertTrue("expected several packets", p.size > 1)
        // No packet exceeds what the characteristic tolerates.
        p.forEach { assertTrue("packet too big: ${it.size}", it.size <= Halo.maxPacket(maxData)) }
        // Every packet carries the header and the code.
        p.forEach {
            assertEquals(Halo.DATA_FLAG, it[0])
            assertEquals(0x0a.toByte(), it[1])
        }
        val (code, back) = Halo.reassemble(p)
        assertEquals(0x0a, code)
        assertArrayEquals(payload, back)
    }

    @Test
    fun `edge cases at the packet boundary stay lossless`() {
        val maxData = 32
        val chunk = maxData - 1
        // exactly the sizes around the transitions: the first packet holds
        // chunk-2 bytes, every later one chunk.
        for (n in listOf(chunk - 3, chunk - 2, chunk - 1, chunk, chunk + 1,
                         2 * chunk - 2, 2 * chunk - 1, 2 * chunk)) {
            val payload = ByteArray(n) { (it and 0x7F).toByte() }
            val p = Halo.packets(0x22, payload, maxData)
            p.forEach { assertTrue("n=$n packet too big", it.size <= Halo.maxPacket(maxData)) }
            val (_, back) = Halo.reassemble(p)
            assertArrayEquals("n=$n came back altered", payload, back)
        }
    }

    @Test
    fun `a payload over 65535 bytes is rejected`() {
        var thrown = false
        try {
            Halo.packets(0x0a, ByteArray(65536), maxDataLength = 241)
        } catch (e: IllegalArgumentException) {
            thrown = true
        }
        assertTrue("an oversized message has to be noticed", thrown)
    }

    // ---- Session ---------------------------------------------------------

    @Test
    fun `the session writes text in the SDK template's format`() {
        val link = FakeLink()
        val s = HaloSession(link)
        s.showText("the agent's reply", x = 1, y = 1, color = 1, spacing = 4)
        assertEquals(1, link.packets.size)
        val (code, payload) = Halo.reassemble(link.packets)
        assertEquals(HaloSession.Code.TEXT, code)
        // header as in plain_text.lua: x, y (16 bit each), colour, line spacing
        assertEquals(1, ((payload[0].toInt() and 0xFF) shl 8) or (payload[1].toInt() and 0xFF))
        assertEquals(1, ((payload[2].toInt() and 0xFF) shl 8) or (payload[3].toInt() and 0xFF))
        assertEquals(1, payload[4].toInt())
        assertEquals(4, payload[5].toInt())
        assertEquals("the agent's reply",
            String(payload, 6, payload.size - 6, Charsets.UTF_8))
    }

    @Test
    fun `clearing the display sends the code without a body`() {
        val link = FakeLink()
        HaloSession(link).clear()
        val (code, payload) = Halo.reassemble(link.packets)
        assertEquals(HaloSession.Code.CLEAR, code)
        assertEquals(0, payload.size)
    }

    @Test
    fun `the microphone start carries sample rate and bit depth`() {
        val link = FakeLink()
        HaloSession(link).startAudio(sampleRate = 8000, bitDepth = 16)
        val (code, payload) = Halo.reassemble(link.packets)
        assertEquals(HaloSession.Code.AUDIO_START, code)
        assertEquals(3, payload.size)
        assertEquals(8000, ((payload[0].toInt() and 0xFF) shl 8) or (payload[1].toInt() and 0xFF))
        assertEquals(16, payload[2].toInt())
    }

    @Test
    fun `long text is split into several packets and stays readable`() {
        val link = FakeLink(mtu = 100)
        val s = HaloSession(link)
        val text = "A long reply. ".repeat(60)            // ~800 characters
        s.showText(text, limit = 2000)
        assertTrue(link.packets.size > 1)
        val (_, payload) = Halo.reassemble(link.packets)
        assertEquals(text, String(payload, 6, payload.size - 6, Charsets.UTF_8))
    }

    @Test
    fun `a Lua module goes in bites and is stored on the glasses`() {
        val link = FakeLink(mtu = 100)
        HaloSession(link).uploadLua("plain_text", "x".repeat(500))
        assertTrue("expected several lines", link.strings.size > 3)
        assertTrue(link.strings.first().startsWith("__m="))
        assertTrue(link.strings.last().contains("plain_text.lua"))
        assertTrue(link.strings.last().contains("f:close()"))
        // All the bites together add up to the source again.
        val body = link.strings.filter { it.contains("[==[") }
            .joinToString("") { it.substringAfter("[==[").substringBeforeLast("]==]") }
        assertEquals("x".repeat(500), body)
    }

    // ---- Audio -----------------------------------------------------------

    @Test
    fun `audio chunks are collected until the final one arrives`() {
        val c = Halo.AudioCollector()
        c.feed(byteArrayOf(Halo.AUDIO_CHUNK, 1, 2, 3))
        assertTrue("not finished yet", !c.complete)
        val live = c.feed(byteArrayOf(Halo.AUDIO_CHUNK, 4, 5))
        assertArrayEquals(byteArrayOf(4, 5), live)
        c.feed(byteArrayOf(Halo.AUDIO_FINAL, 6))
        assertTrue("the final chunk has to end it", c.complete)
        assertArrayEquals(byteArrayOf(1, 2, 3, 4, 5, 6), c.pcm())
    }

    @Test
    fun `foreign responses do not end up in the recording`() {
        val c = Halo.AudioCollector()
        c.feed(byteArrayOf(Halo.AUDIO_CHUNK, 9))
        c.feed(byteArrayOf(0x02, 7, 7, 7))            // e.g. a tap event
        c.feed(byteArrayOf(Halo.AUDIO_FINAL, 8))
        assertArrayEquals(byteArrayOf(9, 8), c.pcm())
    }

    @Test
    fun `the WAV header describes the data correctly`() {
        val pcm = ByteArray(1000) { 7 }
        val w = Halo.wav(pcm, sampleRate = 8000, bitsPerSample = 16, channels = 1)
        assertEquals(44 + pcm.size, w.size)
        assertEquals("RIFF", String(w, 0, 4, Charsets.US_ASCII))
        assertEquals("WAVE", String(w, 8, 4, Charsets.US_ASCII))
        assertEquals("fmt ", String(w, 12, 4, Charsets.US_ASCII))
        assertEquals("data", String(w, 36, 4, Charsets.US_ASCII))
        fun le32(at: Int) = (w[at].toInt() and 0xFF) or ((w[at + 1].toInt() and 0xFF) shl 8) or
                ((w[at + 2].toInt() and 0xFF) shl 16) or ((w[at + 3].toInt() and 0xFF) shl 24)
        fun le16(at: Int) = (w[at].toInt() and 0xFF) or ((w[at + 1].toInt() and 0xFF) shl 8)
        assertEquals(36 + pcm.size, le32(4))          // file size minus the first 8 bytes
        assertEquals(16, le32(16))                    // PCM header
        assertEquals(1, le16(20))                     // uncompressed
        assertEquals(1, le16(22))                     // mono
        assertEquals(8000, le32(24))                  // sample rate
        assertEquals(16000, le32(28))                 // bytes per second
        assertEquals(2, le16(32))                     // block align
        assertEquals(16, le16(34))                    // bits per sample
        assertEquals(pcm.size, le32(40))
    }

    // ---- Acknowledgements -------------------------------------------------

    @Test
    fun `data frames and interpreter text are told apart`() {
        // The glasses send both over the same characteristic.
        val ackWire = byteArrayOf(0x01, 0x00, 0x00)
        val luaText = "error in line 3".toByteArray()
        assertTrue(Halo.isDataFrame(ackWire))
        assertTrue(!Halo.isDataFrame(luaText))
        assertArrayEquals(byteArrayOf(0x00, 0x00), Halo.frameOf(ackWire))
        assertArrayEquals(luaText, Halo.frameOf(luaText))     // text is left alone
    }

    @Test
    fun `packet acknowledgements are recognised and told from payload`() {
        // On the wire 01 00 00 -> in the frame 00 00.
        assertTrue(Halo.isAck(Halo.frameOf(byteArrayOf(0x01, 0x00, 0x00))))
        assertTrue(Halo.isAck(Halo.frameOf(byteArrayOf(0x01, 0x00, 0x01))))
        assertTrue(Halo.isAckError(Halo.frameOf(byteArrayOf(0x01, 0x00, 0x01))))
        assertTrue(!Halo.isAckError(Halo.frameOf(byteArrayOf(0x01, 0x00, 0x00))))
        // Audio and photo responses must not pass as acknowledgements.
        assertTrue(!Halo.isAck(byteArrayOf(Halo.AUDIO_CHUNK, 1, 2)))
        assertTrue(!Halo.isAck(byteArrayOf(Halo.PHOTO_FINAL, 1, 2)))
        assertTrue(!Halo.isAck(byteArrayOf(Halo.ACK_FLAG)))   // too short
    }

    @Test
    fun `an 8-bit recording is shifted to unsigned for WAV`() {
        val signed = byteArrayOf(0, 127, -128, -1)
        val unsigned = Halo.signed8ToUnsigned8(signed)
        assertEquals(128, unsigned[0].toInt() and 0xFF)   // 0 -> the middle
        assertEquals(255, unsigned[1].toInt() and 0xFF)   // maximum
        assertEquals(0, unsigned[2].toInt() and 0xFF)     // minimum
        assertEquals(127, unsigned[3].toInt() and 0xFF)
    }
}
