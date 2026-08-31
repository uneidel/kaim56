// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Protocol layer for the Brilliant Labs glasses (Halo/Frame).
//
// Ported from the Brilliant SDK (BSD-3-Clause, (c) 2025 CitizenOneX,
// github.com/brilliantlabsAR/brilliant_sdk, package `brilliant_ble`). Why
// ported instead of pulled in: the official mobile SDK is Flutter/Dart. A
// second runtime just for a BLE client would be a foreign body in this Compose
// app — the protocol itself is small.
//
// This file is deliberately FREE of Android dependencies: it can be tested
// without a device and without an emulator (see app/src/test/.../HaloTest.kt).
// The Bluetooth part sits behind HaloLink and comes separately.
package de.kat56.agent

/** Framing, addresses and helpers of the glasses connection. */
object Halo {

    // ---- GATT ------------------------------------------------------------
    /** Service under which both glasses offer their data channels. */
    const val SERVICE = "7a230001-5475-a6a4-654c-8431f6ad49c4"
    /** Phone -> glasses. */
    const val CHAR_TX = "7a230002-5475-a6a4-654c-8431f6ad49c4"
    /** Glasses -> phone (notifications). */
    const val CHAR_RX = "7a230003-5475-a6a4-654c-8431f6ad49c4"
    /** Halo only: its own audio channel. Doubles as device detection — Frame
     *  does not have this characteristic. */
    const val CHAR_AUDIO_TX = "7a230005-5475-a6a4-654c-8431f6ad49c4"
    /** Firmware update runs over its own service (not used here). */
    const val DFU_SERVICE = "8ec90001-f315-4f60-9fb8-838830daea50"

    /** First byte of every data packet: "raw user data" (as opposed to Lua text). */
    const val DATA_FLAG: Byte = 0x01
    /** Audio responses from the glasses: running chunk resp. final chunk. */
    const val AUDIO_CHUNK: Byte = 0x05
    const val AUDIO_FINAL: Byte = 0x06
    /** Photo responses: another chunk resp. final chunk. */
    const val PHOTO_CHUNK: Byte = 0x07
    const val PHOTO_FINAL: Byte = 0x08

    // ---- Receive side ----------------------------------------------------
    // The glasses send two things over the same characteristic:
    //   [0x01, rest…]  -> data (acknowledgement, audio, photo)
    //   anything else  -> text from the Lua interpreter (print, errors)
    // After stripping the 0x01 the data frame starts with its flag. ALL the
    // checks here work on the stripped frame.
    const val ACK_FLAG: Byte = 0x00

    fun isDataFrame(raw: ByteArray): Boolean = raw.isNotEmpty() && raw[0] == DATA_FLAG

    /** Remove the 0x01 header; the result is [flag, rest…]. */
    fun frameOf(raw: ByteArray): ByteArray =
        if (isDataFrame(raw)) raw.copyOfRange(1, raw.size) else raw

    // The glasses acknowledge EVERY packet they receive ("receiver-paced flow
    // control"): only after the acknowledgement may the next one go out, else
    // their buffer overflows. On the wire 01 00 00, in the frame 00 00.
    /** true when the frame is a packet acknowledgement (ok or error). */
    fun isAck(frame: ByteArray): Boolean =
        frame.size >= 2 && frame[0] == ACK_FLAG &&
            (frame[1] == 0x00.toByte() || frame[1] == 0x01.toByte())

    /** true when the glasses acknowledged the packet as faulty. */
    fun isAckError(frame: ByteArray): Boolean = isAck(frame) && frame[1] == 0x01.toByte()

    /** Size we request on connect; the negotiated value may be smaller. */
    const val MTU_REQUEST = 517

    // ---- Sizes -----------------------------------------------------------
    // From the SDK: maxString = mtu-3, maxData = mtu-4. On Halo another -2,
    // because the device reports 517 but really tolerates less.
    fun maxStringLength(mtu: Int, isHalo: Boolean): Int = mtu - 3 - (if (isHalo) 2 else 0)

    fun maxDataLength(mtu: Int, isHalo: Boolean): Int = mtu - 4 - (if (isHalo) 2 else 0)

    /** Largest packet allowed on the TX characteristic. */
    fun maxPacket(maxDataLength: Int): Int = maxDataLength + 1

    // ---- Rahmung ---------------------------------------------------------
    /**
     * Splits a message into BLE packets — byte-identical to the SDK original:
     *
     *   first packet:  [0x01, code, length_hi, length_lo, payload…]
     *   later packets: [0x01, code, payload…]
     *
     * The leading 0x01 is taken off by the glasses' Bluetooth stack before the
     * Lua handler sees the data — there the packet starts with the code.
     *
     * The length is that of the WHOLE payload (16 bit, big endian); the Lua
     * handler on the glasses reassembles the chunks from it. At most 65535
     * bytes of payload.
     */
    fun packets(msgCode: Int, payload: ByteArray, maxDataLength: Int): List<ByteArray> {
        require(payload.size <= 65535) { "payload longer than 65535 bytes" }
        require(maxDataLength >= 8) { "maxDataLength too small: $maxDataLength" }
        val code = (msgCode and 0xFF).toByte()
        val chunk = maxDataLength - 1          // as in the SDK: one byte stays spare
        val out = ArrayList<ByteArray>()
        var sent = 0
        var first = true
        // An empty payload is a valid message (e.g. "clear the display"):
        // one packet with length 0 and no body.
        do {
            val rest = payload.size - sent
            val take = if (first) minOf(rest, chunk - 2) else minOf(rest, chunk)
            val head = if (first) 4 else 2
            val p = ByteArray(head + take)
            p[0] = DATA_FLAG
            p[1] = code
            if (first) {
                p[2] = (payload.size ushr 8).toByte()
                p[3] = (payload.size and 0xFF).toByte()
            }
            payload.copyInto(p, head, sent, sent + take)
            out.add(p)
            sent += take
            first = false
        } while (sent < payload.size)
        return out
    }

    /**
     * Counterpart to [packets] — what the Lua handler on the glasses does.
     * For tests only: it lets the framing be checked without a device.
     * Returns (code, payload) or throws when the stream does not add up.
     */
    fun reassemble(packets: List<ByteArray>): Pair<Int, ByteArray> {
        require(packets.isNotEmpty()) { "no packets" }
        val first = packets.first()
        require(first.size >= 4) { "first packet too short" }
        require(first[0] == DATA_FLAG) { "first packet without the 0x01 header" }
        val code = first[1].toInt() and 0xFF
        val total = ((first[2].toInt() and 0xFF) shl 8) or (first[3].toInt() and 0xFF)
        val buf = ByteArray(total)
        var at = 0
        packets.forEachIndexed { i, p ->
            require(p[0] == DATA_FLAG) { "packet $i without the 0x01 header" }
            require((p[1].toInt() and 0xFF) == code) { "packet $i carries a foreign code" }
            val head = if (i == 0) 4 else 2
            val n = p.size - head
            require(at + n <= total) { "packet $i runs past the announced length" }
            p.copyInto(buf, at, head, p.size)
            at += n
        }
        require(at == total) { "incomplete: $at of $total bytes" }
        return code to buf
    }

    // ---- Audio -----------------------------------------------------------
    /**
     * Collects the audio responses from the glasses. Every notification carries
     * a leading flag: 0x05 = another chunk, 0x06 = final chunk. Everything after
     * it is raw PCM.
     */
    class AudioCollector {
        private val buf = java.io.ByteArrayOutputStream()
        /** true once the final chunk arrived. */
        var complete: Boolean = false
            private set

        /** Handles one packet; returns its PCM part (for live forwarding). */
        fun feed(packet: ByteArray): ByteArray {
            if (packet.isEmpty()) return ByteArray(0)
            when (packet[0]) {
                AUDIO_CHUNK -> {}
                AUDIO_FINAL -> complete = true
                else -> return ByteArray(0)     // not an audio response -> ignore
            }
            val pcm = packet.copyOfRange(1, packet.size)
            buf.write(pcm)
            return pcm
        }

        fun pcm(): ByteArray = buf.toByteArray()
    }

    /**
     * Collects a photo. Like the audio it arrives in pieces: 0x07 = another
     * chunk, 0x08 = the end. The glasses deliver a complete JPEG (the frugal
     * raw mode saves the 623-byte header but demands that the app keeps a
     * header per quality and resolution — not worth the saving).
     */
    class PhotoCollector {
        private val buf = java.io.ByteArrayOutputStream()
        var complete: Boolean = false
            private set

        /** Handles one packet; true when the image is complete with it. */
        fun feed(frame: ByteArray): Boolean {
            if (frame.isEmpty()) return false
            when (frame[0]) {
                PHOTO_CHUNK -> buf.write(frame, 1, frame.size - 1)
                PHOTO_FINAL -> {
                    // The final chunk may be empty — then the image already stands.
                    if (frame.size > 1) buf.write(frame, 1, frame.size - 1)
                    complete = true
                }
                else -> return false
            }
            return complete
        }

        fun jpeg(): ByteArray = buf.toByteArray()

        fun reset() { buf.reset(); complete = false }
    }

    /**
     * Put a WAV header in front of the PCM — that way the recording goes to
     * /api/stt in the manager unchanged, which already handles the app's
     * dictation.
     */
    fun wav(pcm: ByteArray, sampleRate: Int = 8000, bitsPerSample: Int = 16,
            channels: Int = 1): ByteArray {
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val blockAlign = channels * bitsPerSample / 8
        val head = java.io.ByteArrayOutputStream(44)
        fun ascii(s: String) = head.write(s.toByteArray(Charsets.US_ASCII))
        fun le32(v: Int) { head.write(v and 0xFF); head.write((v ushr 8) and 0xFF)
                           head.write((v ushr 16) and 0xFF); head.write((v ushr 24) and 0xFF) }
        fun le16(v: Int) { head.write(v and 0xFF); head.write((v ushr 8) and 0xFF) }
        ascii("RIFF");  le32(36 + pcm.size); ascii("WAVE")
        ascii("fmt ");  le32(16); le16(1)               // 16 = PCM header, 1 = uncompressed
        le16(channels); le32(sampleRate); le32(byteRate); le16(blockAlign); le16(bitsPerSample)
        ascii("data");  le32(pcm.size)
        return head.toByteArray() + pcm
    }

    /**
     * 8-bit recordings from the glasses are SIGNED, but WAV expects unsigned at
     * 8 bit. Hence this shift. (We drive the microphone at 16 bit by default,
     * where the question does not arise — this is for the frugal mode.)
     */
    fun signed8ToUnsigned8(pcm: ByteArray): ByteArray =
        ByteArray(pcm.size) { ((pcm[it].toInt() and 0xFF) xor 0x80).toByte() }
}

/**
 * The Bluetooth substrate, behind an interface: that way everything above it
 * can be tested without a device (the tests put a fake in its place).
 */
interface HaloLink {
    /** Negotiated MTU of the current connection. */
    val mtu: Int
    /** true = Halo (has the audio channel), false = Frame. */
    val isHalo: Boolean
    /** Write one packet to the TX characteristic. */
    fun write(packet: ByteArray)
    /** A line for the glasses' Lua interpreter (without the 0x01 header). */
    fun writeString(text: String)
}

/**
 * A session on connected glasses: knows the negotiated sizes and sends typed
 * messages. The codes have to match the Lua handlers that are loaded onto the
 * glasses when connecting.
 */
class HaloSession(private val link: HaloLink) {

    val maxData: Int get() = Halo.maxDataLength(link.mtu, link.isHalo)
    val maxString: Int get() = Halo.maxStringLength(link.mtu, link.isHalo)

    /** This app's message codes (phone -> glasses). TEXT and CLEAR as in the
     *  SDK template, so its unmodified Lua modules fit. */
    object Code {
        const val TEXT = 0x12           // show text (plain_text.lua)
        const val CLEAR = 0x10          // clear the display (code.lua)
        const val AUDIO_START = 0x30    // microphone on
        const val AUDIO_STOP = 0x31     // microphone off
        const val PHOTO = 0x0d          // take a photo
    }

    fun send(msgCode: Int, payload: ByteArray) {
        Halo.packets(msgCode, payload, maxData).forEach(link::write)
    }

    /**
     * Reply text onto the glasses. The format is the SDK template's, so its
     * `plain_text.lua` can read the body unchanged:
     * [x_hi, x_lo, y_hi, y_lo, colour, line spacing, text…].
     * Cap the length — the display is small, and a message must not exceed
     * 65535 bytes.
     */
    fun showText(text: String, x: Int = 1, y: Int = 1, color: Int = 1,
                 spacing: Int = 4, limit: Int = 1024) {
        val body = text.take(limit).toByteArray(Charsets.UTF_8)
        val p = ByteArray(6 + body.size)
        p[0] = (x ushr 8).toByte(); p[1] = (x and 0xFF).toByte()
        p[2] = (y ushr 8).toByte(); p[3] = (y and 0xFF).toByte()
        p[4] = (color and 0xFF).toByte()      // index into the glasses' palette
        p[5] = (spacing and 0xFF).toByte()
        body.copyInto(p, 6)
        send(Code.TEXT, p)
    }

    fun clear() = send(Code.CLEAR, ByteArray(0))

    /** Start the microphone. Default 8 kHz/16 bit: small enough for BLE, clean
     *  for speech recognition, and no signedness question as at 8 bit. */
    fun startAudio(sampleRate: Int = 8000, bitDepth: Int = 16) {
        send(Code.AUDIO_START, byteArrayOf(
            (sampleRate ushr 8).toByte(), (sampleRate and 0xFF).toByte(), bitDepth.toByte()))
    }

    fun stopAudio() = send(Code.AUDIO_STOP, ByteArray(0))

    fun takePhoto() = send(Code.PHOTO, ByteArray(0))

    /** Push a Lua module onto the glasses (on connect, once per session). */
    fun uploadLua(name: String, source: String) {
        // The interpreter takes lines; longer modules go in bites smaller than
        // the maximum string length.
        val chunk = maxString - 32
        var i = 0
        link.writeString("__m='' ")
        while (i < source.length) {
            val part = source.substring(i, minOf(source.length, i + chunk))
            link.writeString("__m=__m..[==[$part]==] ")
            i += chunk
        }
        link.writeString("f=frame.file.open('$name.lua','w');f:write(__m);f:close();__m=nil ")
    }
}
