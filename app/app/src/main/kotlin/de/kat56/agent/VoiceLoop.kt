// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.os.Build
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// Speech-pause detection, everything in milliseconds. VAD_HANG is the one value
// you feel: too short and it cuts off mid-sentence, too long and you wait after
// every sentence.
private const val VAD_TICK = 100L
private const val VAD_HANG = 3500L   // longer pauses for thought allowed (natural speech)
private const val VAD_LEAD = 6000L      // never said anything -> abort
private const val VAD_MAX = 120_000L    // emergency brake against an endless recording

/**
 * Voice in and out: record on the device (stops on its own when it goes
 * quiet), recognize and speak on the manager (Parakeet/Piper), barge-in (an
 * echo-cancelled mic listens while a reply is spoken; talking over it cuts it
 * off and becomes the next input).
 *
 *  state   recording, transcribing, speakingIdx (-1 = silent), bargeListening
 *  calls   startRec, stopRec, speak, stopSpeak, stopBargeMic
 *
 * [onHeard] gets each recognized utterance, [onStatus] a hint, [post] runs a
 * block on the main thread (callbacks of player and mic come from elsewhere).
 */
class VoiceLoop(
    private val context: Context,
    private val prefs: Prefs,
    private val scope: CoroutineScope,
    private val post: (() -> Unit) -> Unit,
    private val onStatus: (String) -> Unit,
    private val onHeard: (String) -> Unit,
    private val client: () -> ManagerClient = { ManagerClient(prefs) },
) {
    var recording by mutableStateOf(false); private set
    var transcribing by mutableStateOf(false); private set
    var speakingIdx by mutableStateOf(-1); private set      // which message is being spoken
    var bargeListening by mutableStateOf(false); private set

    private var recorder: MediaRecorder? = null
    private var recFile: java.io.File? = null
    private var player: TtsPlayer? = null
    private var echoMic: EchoMic? = null
    // A counter instead of a flag: synthesis runs over the network, and a reply
    // that trickles in after cancellation must not still blare out.
    private var speakGen = 0

    fun stopSpeak() {
        speakGen++
        runCatching { player?.stop() }
        player = null
        speakingIdx = -1
    }

    fun stopBargeMic() {
        echoMic?.stop(); echoMic = null
        bargeListening = false
    }

    /** Recognize on the manager and hand the text on (hands-free). */
    fun transcribe(audio: ByteArray, mime: String) {
        transcribing = true
        scope.launch {
            val mc = client()               // its own client: the error belongs to this call
            val text = withContext(Dispatchers.IO) { mc.stt(audio, mime) }
            transcribing = false
            if (text.isNullOrBlank()) { onStatus("Didn't catch that (${mc.lastError})"); return@launch }
            onHeard(text)
        }
    }

    /** Speak [text] (bubble [idx] shows it). [bargeIn]: keep listening while
     *  speaking (voice turns only, never for read-aloud). */
    fun speak(text: String, idx: Int = -1, bargeIn: Boolean = false) {
        if (text.isBlank() || prefs.serverUrl.isBlank()) return
        stopSpeak()                       // never two voices at once
        stopBargeMic()
        val gen = speakGen
        speakingIdx = idx
        scope.launch {
            val mc = client()
            val wav = withContext(Dispatchers.IO) { mc.tts(text.take(4000)) }
            if (gen != speakGen) return@launch          // cancelled in the meantime
            if (wav == null) { speakingIdx = -1; onStatus("⚠️ Speech: ${mc.lastError}"); return@launch }
            withContext(Dispatchers.IO) {
                runCatching {
                    if (gen != speakGen) return@runCatching
                    val pcm = Wav.parse(wav)
                    var tp: TtsPlayer? = null
                    // the player reports on the main looper — safe for the Compose state
                    tp = TtsPlayer(onDone = {
                        if (player === tp) { player = null; speakingIdx = -1 }
                        echoMic?.playbackEnded()
                    })
                    player?.stop()
                    player = tp
                    var mic: EchoMic? = null
                    if (bargeIn && prefs.bargeIn && pcm != null && !recording &&
                        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                        PackageManager.PERMISSION_GRANTED) {
                        // each callback checks it still is THE mic: a stopped one may report late
                        mic = EchoMic(pcm.rate,
                            onBargeIn = { if (echoMic === mic) { stopSpeak(); bargeListening = true; onStatus("Listening…") } },
                            onUtterance = { bytes -> if (echoMic === mic) { echoMic = null; bargeListening = false; transcribe(bytes, "audio/wav") } },
                            onIdle = { if (echoMic === mic) { echoMic = null; bargeListening = false } })
                        if (!mic.start()) mic = null
                    }
                    echoMic = mic
                    val farEnd: ((ShortArray, Int) -> Unit)? = mic?.let { m -> { buf, n -> m.feedFarEnd(buf, n) } }
                    if (!tp.play(wav, farEnd)) {
                        mic?.stop(); echoMic = null; player = null
                        post { speakingIdx = -1; onStatus("⚠️ Speech: unsupported audio") }
                    }
                }
            }
        }
    }

    fun stopRec() {
        val r = recorder ?: return
        recorder = null; recording = false
        // stop() throws if stopped too early (too short) — then there is nothing to recognize
        val ok = runCatching { r.stop() }.isSuccess
        runCatching { r.release() }
        val f = recFile; recFile = null
        if (!ok || f == null || !f.exists() || f.length() < 2000) { onStatus("Too short — try again"); f?.delete(); return }
        transcribing = true
        scope.launch {
            val bytes = withContext(Dispatchers.IO) { f.readBytes().also { f.delete() } }
            transcribe(bytes, "audio/mp4")
        }
    }

    /** Start recording (the caller checked the RECORD_AUDIO permission). */
    fun startRec() {
        if (prefs.serverUrl.isBlank()) { onStatus("⚠️ Server URL missing (Settings)"); return }
        stopSpeak()                       // speaking over it means: the output is done
        stopBargeMic()
        val f = java.io.File(context.cacheDir, "rec.m4a")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
        val ok = runCatching {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioSamplingRate(16000)      // recognition needs no more
            r.setAudioChannels(1)
            r.setAudioEncodingBitRate(32000)
            r.setOutputFile(f.absolutePath)
            r.prepare(); r.start()
        }.isSuccess
        if (!ok) { runCatching { r.release() }; onStatus("⚠️ Cannot record"); return }
        recorder = r; recFile = f; recording = true
        onStatus("Listening… stops on its own")
        // Stop by itself when it goes quiet. The noise floor comes from the first
        // ~0.6 s as a MINIMUM (a train is louder than an office, and someone who
        // starts talking at once must not lift it); hysteresis keeps the short dips
        // between words from counting as silence.
        scope.launch {
            var floor = Int.MAX_VALUE; var probes = 0
            var spoke = false; var quiet = 0L; var total = 0L
            while (recorder === r) {
                delay(VAD_TICK)
                total += VAD_TICK
                val amp = runCatching { r.maxAmplitude }.getOrDefault(0)
                if (probes < 6) { floor = minOf(floor, amp); probes++; continue }
                val base = (if (floor == Int.MAX_VALUE) 0 else floor).coerceAtMost(3000)
                val loud = if (spoke) amp > base + 350 else amp > base + 1500
                if (loud) { spoke = true; quiet = 0L } else if (spoke) quiet += VAD_TICK
                val done = (spoke && quiet >= VAD_HANG) || (!spoke && total >= VAD_LEAD) || total >= VAD_MAX
                if (done) { if (recorder === r) stopRec(); return@launch }
            }
        }
    }
}
