// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Voice commands: whatever speech recognition returns is checked HERE first
// and only sent to the agent when no local command is in it. Reason: "take a
// screenshot" has to be done by the phone, not answered by the model.
//
// Pure text handling, no Android dependency — so it is checkable without
// recording anything.
package de.kat56.agent

object VoiceCommand {

    enum class Action {
        /** Capture the phone's screen. */
        SCREENSHOT,
        /** Photo with the glasses' camera. */
        PHOTO,
        /** Cancel the running reply/recording. */
        STOP,
    }

    /** [rest] is the text without the command — the question about the image,
     *  if one was spoken along ("screenshot, what does it say?"). */
    data class Parsed(val action: Action, val rest: String)

    // Triggers, German and English. Deliberately kept short: the more phrases
    // are recognised, the more often the matcher swallows a real question meant
    // for the agent.
    private val triggers = listOf(
        Action.SCREENSHOT to listOf(
            "screenshot", "bildschirmfoto", "bildschirm aufnehmen",
            "screen shot", "capture screen"),
        Action.PHOTO to listOf(
            "foto", "photo", "bild aufnehmen", "take a picture", "take a photo",
            "kamera", "camera"),
        Action.STOP to listOf(
            "stopp", "stop", "abbrechen", "halt", "cancel"),
    )

    // Pleasantries that may stand in front of the command.
    private val fillers = listOf(
        "bitte", "mal", "mir", "einen", "eine", "ein", "das", "den", "die",
        "kannst du", "koenntest du", "kannst du mal", "please", "can you",
        "could you", "just", "a", "an", "the",
    )

    /**
     * Looks for a command at the START of the utterance. Only there: mid
     * sentence ("… and then there was a photo of grandma") it would almost
     * always be wrong. Returns null = no command, the text belongs to the agent.
     */
    fun parse(text: String): Parsed? {
        var s = normalize(text)
        if (s.isEmpty()) return null
        // Clear leading verbs and pleasantries: "please take a …"
        var changed = true
        while (changed) {
            changed = false
            for (w in listOf("mach", "mache", "machen", "nimm", "nimm auf",
                             "erstelle", "erstell", "zeig", "zeige", "make", "take") + fillers) {
                if (s == w) return null                     // only filler, no command
                if (s.startsWith("$w ")) { s = s.removePrefix("$w ").trim(); changed = true }
            }
        }
        for ((action, words) in triggers) {
            for (w in words) {
                if (s == w) return Parsed(action, "")
                if (s.startsWith("$w ")) return Parsed(action, cleanRest(s.removePrefix("$w ")))
                // "screenshot, what does it say" — the comma is dropped in
                // normalize(), what stays here is the remainder.
            }
        }
        return null
    }

    /** Lowercase, punctuation gone, runs of spaces collapsed. */
    private fun normalize(text: String): String =
        text.lowercase()
            .replace(Regex("[.,!?;:]+"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()

    /** Strip fillers at the start of the remaining question ("and what does it say"). */
    private fun cleanRest(rest: String): String {
        var r = rest.trim()
        for (w in listOf("und", "dann", "and", "then")) {
            if (r.startsWith("$w ")) r = r.removePrefix("$w ").trim()
        }
        return r
    }
}
