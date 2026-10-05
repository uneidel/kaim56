// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

/**
 * Merge the manager's chats into the local ones — the ONE place for it (the
 * full sync and the live long-poll both use it).
 *
 * Rules:
 *  - a chat tombstoned locally at or after its remote updatedAt stays deleted;
 *  - a remote chat we do not have is added;
 *  - the local side wins when it is newer, and the chat in `busyId` (a turn is
 *    streaming into it) is not touched;
 *  - otherwise messages are only APPENDED when the local list is a prefix of
 *    the remote one (a just-typed, not yet pushed question survives); on a real
 *    divergence the remote state is adopted if it is not shorter; a longer
 *    local list is kept (the next push reconciles it).
 *
 * Existing Conversation objects are FILLED, never replaced: send() holds a
 * reference to current.messages and streams the reply into it — a swapped
 * object would take question and answer into a detached list.
 */
object ChatMerge {
    /** conversations: the merged list (local objects reused), newest first. */
    data class Result(val conversations: List<Conversation>, val changed: Boolean)

    fun merge(local: List<Conversation>, remote: List<Conversation>, tombs: Map<String, Long>,
              busyId: String? = null): Result {
        val byId = LinkedHashMap<String, Conversation>()
        for (c in local) byId[c.id] = c
        var changed = false
        for (r in remote) {
            if (tombs[r.id]?.let { r.updatedAt <= it } == true) continue
            val l = byId[r.id]
            if (l == null) { byId[r.id] = r; changed = true; continue }
            if (r.updatedAt <= l.updatedAt) continue
            if (r.id == busyId) continue
            val lm = l.messages
            val rm = r.messages
            val isPrefix = rm.size >= lm.size && lm.indices.all { lm[it] == rm[it] }
            when {
                isPrefix -> if (rm.size > lm.size) { for (i in lm.size until rm.size) lm.add(rm[i]); changed = true }
                rm.size >= lm.size -> { lm.clear(); lm.addAll(rm); changed = true }
                else -> continue
            }
            if (l.title != r.title && r.title.isNotBlank()) { l.title = r.title; changed = true }
            if (l.instance != r.instance && r.instance.isNotBlank()) l.instance = r.instance
            l.updatedAt = r.updatedAt
        }
        return Result(byId.values.sortedByDescending { it.updatedAt }, changed)
    }
}
