// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.content.Context
import android.graphics.Bitmap
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.SamplerConfig
import kotlinx.coroutines.flow.collect
import java.io.File

/** On-device LLM via LiteRT-LM. Loads .litertlm models (incl. Gemma 4).
 *  gemma-3n/gemma-4 are multimodal -> optional image input. */
class LocalGemma(private val context: Context) {
    private var engine: Engine? = null
    var loadedPath: String = ""
        private set

    fun isReady(): Boolean = engine != null

    /** Load a model (path to a .litertlm file). May take several seconds. */
    fun load(modelPath: String) {
        close()
        val cfg = EngineConfig(
            modelPath = modelPath,
            backend = Backend.CPU(),
            visionBackend = Backend.CPU(),   // enable image input (multimodal)
        )
        val e = Engine(cfg)
        e.initialize()
        engine = e
        loadedPath = modelPath
    }

    /** Streaming: onPartial for each chunk, onDone at the end. */
    /** Streaming via Flow: calls onPartial per token; returns when finished.
     *  The caller then sets 'busy=false' (in finally) -> no longer hangs. */
    suspend fun generateStreaming(prompt: String, image: Bitmap?, onPartial: (String) -> Unit) {
        val eng = engine ?: throw IllegalStateException("No model loaded.")
        val conversation = eng.createConversation(
            ConversationConfig(samplerConfig = SamplerConfig(topK = 40, topP = 0.95, temperature = 0.8))
        )
        try {
            val flow = if (image != null) {
                val imgFile = File(context.cacheDir, "input_image.jpg")
                imgFile.outputStream().use { image.compress(Bitmap.CompressFormat.JPEG, 90, it) }
                conversation.sendMessageAsync(Contents.of(Content.ImageFile(imgFile.absolutePath), Content.Text(prompt)))
            } else {
                conversation.sendMessageAsync(prompt)
            }
            flow.collect { onPartial(it.toString()) }
        } finally {
            try { conversation.close() } catch (_: Exception) {}
        }
    }

    fun close() {
        try {
            engine?.close()
        } catch (_: Exception) {
        }
        engine = null
        loadedPath = ""
    }
}
