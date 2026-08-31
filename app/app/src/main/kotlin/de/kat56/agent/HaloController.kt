// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The glasses on the agent: connect, upload the Lua modules, click ->
// recording -> speech recognition -> instance -> reply on the display.
//
// Deliberately WITHOUT Android dependencies and without networking: everything
// that comes from outside (Bluetooth, speech recognition, the agent, the Lua
// files) sits behind functions the caller provides. That way the whole flow can
// be walked dry — with HaloDryLink instead of real glasses.
package de.kat56.agent

/**
 * A fake of the glasses for the dry run: takes the same packets as the real
 * ones, reassembles them and keeps track of what would be on the display. That
 * makes the whole path clickable without hardware.
 */
class HaloDryLink(
    override val mtu: Int = 247,
    override val isHalo: Boolean = true,
    /** What the camera delivers in the dry run. */
    private val cannedPhoto: ByteArray = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte()),
) : HaloLink {
    private val pending = mutableListOf<ByteArray>()

    /** The last "captured" photo, null while none was triggered. */
    var photo: ByteArray? = null
        private set

    /** What would currently be on the glasses' display (line by line). */
    val display = mutableListOf<String>()
    /** Uploaded Lua modules, in upload order. */
    val uploaded = mutableListOf<String>()
    /** Is the microphone running? */
    var recording = false
        private set
    /** Every received message as (code, payload) — for tests. */
    val messages = mutableListOf<Pair<Int, ByteArray>>()

    override fun write(packet: ByteArray) {
        pending.add(packet)
        // A packet completing a message? Then evaluate and clear.
        val done = runCatching { Halo.reassemble(pending.toList()) }.getOrNull() ?: return
        pending.clear()
        messages.add(done)
        val (code, payload) = done
        when (code) {
            HaloSession.Code.TEXT -> {
                val text = String(payload, 6, payload.size - 6, Charsets.UTF_8)
                display.clear()
                display.addAll(text.split("\n").filter { it.isNotEmpty() })
            }
            HaloSession.Code.CLEAR -> display.clear()
            HaloSession.Code.AUDIO_START -> recording = true
            HaloSession.Code.AUDIO_STOP -> recording = false
            HaloSession.Code.PHOTO -> photo = cannedPhoto
        }
    }

    override fun writeString(text: String) {
        // The end of an upload reveals the module name: …open('name.lua','w')…
        Regex("""open\('([^']+)\.lua'""").find(text)?.let { uploaded.add(it.groupValues[1]) }
    }
}

/**
 * Flow and state of the glasses integration. Knows neither Bluetooth nor HTTP —
 * the caller hands both in.
 */
class HaloController(
    /** Supplies the source of a Lua module (from the assets). */
    private val luaSource: (String) -> String,
    /** Recording -> recognised text (in the manager: /api/stt). Null = failed. */
    private val transcribe: (ByteArray) -> String?,
    /** Hand the recognised text to the instance; returns the reply. */
    private val ask: (String) -> String,
    /** A question WITH an image for the instance (JPEG as the glasses deliver it). */
    private val askWithImage: (String, ByteArray) -> String = { q, _ -> ask(q) },
    /** Waits for the next finished photo; null = none arrived. */
    private val awaitPhoto: (Long) -> ByteArray? = { null },
    /** Progress for the UI. */
    private val onStatus: (String) -> Unit = {},
) {
    /** Modules that go onto the glasses when connecting. Order matters:
     *  katagent.lua needs the others at startup. */
    val modules = listOf("data.min", "plain_text.min", "audio.min", "camera.min",
                         "code.min", "katagent")

    var session: HaloSession? = null
        private set

    // Measured on the vendor's emulator (tools/halo/emu_test.py), not guessed:
    // 59 'M' fit side by side across the 256 px width, and 13 lines fit below
    // each other at 20 px line spacing. A little margin is left.
    /** Characters per line. */
    var columns = 56
    /** Lines that fit on the display. */
    var rows = 12

    fun attach(link: HaloLink) {
        val s = HaloSession(link)
        session = s
        onStatus("loading modules…")
        modules.forEach { s.uploadLua(it, luaSource(it)) }
        s.clear()
        onStatus("glasses ready")
    }

    fun detach() {
        session = null
    }

    /** Start recording (a click on the glasses or a button in the app). */
    fun startListening() {
        val s = session ?: return
        s.showText("… listening")
        s.startAudio()
        onStatus("recording")
    }

    /**
     * End the recording and walk the whole path: recording -> speech
     * recognition -> instance -> reply on the display. Blocks; belongs on a
     * background thread. Returns the reply or null.
     */
    fun stopAndAsk(recordingWav: ByteArray): String? {
        val s = session ?: return null
        s.stopAudio()
        onStatus("speech recognition…")
        val heard = transcribe(recordingWav)
        if (heard.isNullOrBlank()) {
            s.showText("did not catch that")
            onStatus("did not catch that")
            return null
        }
        return handle(heard)
    }

    /**
     * Carry out one utterance: first check whether a local command is in it
     * (photo, cancel), otherwise it goes to the instance as a question.
     */
    fun handle(heard: String): String? {
        val s = session ?: return null
        when (VoiceCommand.parse(heard)?.action) {
            VoiceCommand.Action.PHOTO -> return photoAndAsk(VoiceCommand.parse(heard)!!.rest)
            VoiceCommand.Action.STOP -> {
                s.stopAudio()
                s.clear()
                onStatus("cancelled")
                return null
            }
            // A screenshot of the phone: not wired up yet. Better to say so than
            // to quietly do nothing.
            VoiceCommand.Action.SCREENSHOT -> {
                show("I cannot do screenshots yet")
                onStatus("the screenshot is not wired up yet")
                return null
            }
            null -> {}
        }
        onStatus("question: $heard")
        s.showText(wrap("> $heard"))
        val answer = ask(heard)
        show(answer)
        onStatus("")
        return answer
    }

    /**
     * Take a photo with the glasses' camera and send it with the question to
     * the instance. Without a question spoken along, the obvious one is asked.
     */
    fun photoAndAsk(question: String = "", timeoutMs: Long = 20000): String? {
        val s = session ?: return null
        onStatus("photo…")
        s.showText("… photo")
        s.takePhoto()
        val jpeg = awaitPhoto(timeoutMs)
        if (jpeg == null || jpeg.isEmpty()) {
            show("no image received")
            onStatus("no image received")
            return null
        }
        val q = question.ifBlank { "What do you see in this image?" }
        onStatus("image to the agent…")
        val answer = askWithImage(q, jpeg)
        show(answer)
        onStatus("")
        return answer
    }

    /** Put a reply on the glasses, wrapped and cut to the display height. */
    fun show(text: String, maxLines: Int = rows) {
        val s = session ?: return
        val lines = wrap(text).split("\n")
        s.showText(lines.take(maxLines).joinToString("\n"))
    }

    /**
     * Line wrapping for the display. The glasses do not wrap by themselves — if
     * the text arrived in one piece it would simply run off the right edge.
     */
    fun wrap(text: String, width: Int = columns): String {
        val out = StringBuilder()
        for (para in text.split("\n")) {
            if (para.isEmpty()) { out.append('\n'); continue }
            var line = StringBuilder()
            for (word in para.split(" ")) {
                // Break a single overlong word (URL, path) hard.
                var w = word
                while (w.length > width) {
                    if (line.isNotEmpty()) { out.append(line).append('\n'); line = StringBuilder() }
                    out.append(w.substring(0, width)).append('\n')
                    w = w.substring(width)
                }
                when {
                    line.isEmpty() -> line.append(w)
                    line.length + 1 + w.length <= width -> line.append(' ').append(w)
                    else -> { out.append(line).append('\n'); line = StringBuilder(w) }
                }
            }
            out.append(line).append('\n')
        }
        return out.toString().trimEnd('\n')
    }
}
