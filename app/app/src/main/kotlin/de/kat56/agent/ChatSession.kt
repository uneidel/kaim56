// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.graphics.Bitmap
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * The chats and their turns — everything KatAgentApp used to keep in its own
 * closures, now behind one interface the UI only reads and calls:
 *
 *  state   conversations, current, currentId, busy, online, syncing, lastSync
 *  chats   newChat, openChat, openAgentChat, switchToAgent, deleteChat, persist
 *  sync    sync() (full), runLiveSync() (long-poll loop), runRecovery() (loop)
 *  turns   send(), cancelTurn(), handleSlash()
 *
 * The state is Compose state, so the UI recomposes on change. [post] runs a
 * block on the main thread (tests: run it at once), [onStatus] shows a hint,
 * [client] is the manager (tests: one against a local server).
 */
class ChatSession(
    private val prefs: Prefs,
    private val store: ChatStore,
    private val scope: CoroutineScope,
    private val gemma: LocalGemma? = null,
    private val post: (() -> Unit) -> Unit = { it() },
    private val onStatus: (String) -> Unit = {},
    private val client: () -> ManagerClient = { ManagerClient(prefs) },
) {
    val conversations: SnapshotStateList<Conversation> = mutableStateListOf<Conversation>().also { it.addAll(store.load()) }
    private val tombs = store.loadTombs()                  // delete tombstones {id -> deletedAt}

    init { if (conversations.isEmpty()) conversations.add(Conversation(mode = prefs.mode)) }

    var currentId by mutableStateOf(
        prefs.currentChatId.takeIf { id -> conversations.any { it.id == id } } ?: conversations.first().id)
    val current: Conversation get() = conversations.firstOrNull { it.id == currentId } ?: conversations.first()
    var busy by mutableStateOf(false)
    var online by mutableStateOf(false)
    var syncing by mutableStateOf(false)
    var lastSync by mutableStateOf("")

    private var lastStreamSave = 0L
    private var pushJob: Job? = null
    private var turnGen = 0
    private var cancelHandle: ServerAgent.CancelHandle? = null
    private var chatsRev = 0L
    private val recoverTried = HashMap<String, Long>()     // turn -> first attempt

    // ---- chats ------------------------------------------------------------------
    /** Debounced background push: new/changed chats land on the manager (and in
     *  the web UI) live. The manager MERGES, so it overwrites nothing. */
    fun pushChats() {
        if (prefs.serverUrl.isBlank()) return
        pushJob?.cancel()
        pushJob = scope.launch {
            delay(1200)
            withContext(Dispatchers.IO) { client().push(store.toPushJson(conversations, tombs)) }
        }
    }

    fun persist() {
        val c = current
        c.updatedAt = System.currentTimeMillis()
        if (c.title == "New Chat") {
            c.messages.firstOrNull { it.user }?.text?.trim()?.take(40)?.let { if (it.isNotBlank()) c.title = it }
        }
        store.save(conversations)
        pushChats()
    }

    fun openChat(id: String) { currentId = id; prefs.currentChatId = id }

    fun newChat() {
        val c = Conversation(mode = prefs.mode)
        conversations.add(0, c)
        openChat(c.id)
        store.save(conversations)
    }

    /** Switch agent = jump to THIS agent's history (one thread per agent); an
     *  empty current chat is simply reassigned. */
    fun switchToAgent(mode: String, instance: String) {
        prefs.mode = mode; prefs.instance = instance
        val cur = current
        if (cur.messages.isEmpty()) { cur.mode = mode; cur.instance = instance; persist(); return }
        val existing = conversations.filter { it.mode == mode && (mode == "local" || it.instance == instance) }
            .maxByOrNull { it.updatedAt }
        openChat(existing?.id ?: Conversation(mode = mode, instance = instance)
            .also { conversations.add(0, it); store.save(conversations) }.id)
    }

    /** The newest server chat with [instance] (or a new one). */
    fun openAgentChat(instance: String) {
        val existing = conversations.filter { it.mode == "server" && it.instance == instance }.maxByOrNull { it.updatedAt }
        openChat(existing?.id ?: Conversation(mode = "server", instance = instance)
            .also { conversations.add(0, it); store.save(conversations) }.id)
    }

    /** A notification's "chat:<inst>": the task chat (where results land), else
     *  the newest chat with it; created if there is none, so the tap always lands. */
    fun openTaskChat(inst: String) {
        prefs.mode = "server"; prefs.instance = inst
        val existing = conversations.firstOrNull { it.id == "task-$inst" }
            ?: conversations.filter { it.instance == inst }.maxByOrNull { it.updatedAt }
        openChat(existing?.id ?: run {
            val c = Conversation(id = "task-$inst", title = "Tasks \u00b7 $inst",
                mode = "server", instance = inst, updatedAt = System.currentTimeMillis())
            conversations.add(0, c); store.save(conversations); c.id
        })
    }

    fun deleteChat(c: Conversation) {
        tombs[c.id] = System.currentTimeMillis()   // propagate the deletion (web + other devices)
        store.saveTombs(tombs)
        conversations.remove(c)
        if (conversations.isEmpty()) conversations.add(Conversation(mode = prefs.mode))
        if (currentId == c.id) openChat(conversations.first().id)
        store.save(conversations)
        pushChats()
    }

    // ---- sync ---------------------------------------------------------------------
    fun sync() {
        if (prefs.serverUrl.isBlank()) { onStatus("⚠️ Server URL missing (Settings)"); return }
        scope.launch {
            syncing = true
            onStatus("Sync…")
            val remoteJson = withContext(Dispatchers.IO) { client().pull() }
            if (remoteJson == null) {
                syncing = false; online = false
                onStatus("⚠️ Sync: server unreachable"); return@launch
            }
            // same rules as the live poll (ChatMerge): fill, never replace
            val merged = ChatMerge.merge(conversations, store.fromJson(remoteJson), tombs,
                                         busyId = if (busy) currentId else null).conversations
            conversations.clear(); conversations.addAll(merged)
            if (conversations.none { it.id == currentId }) {
                if (conversations.isEmpty()) conversations.add(Conversation(mode = prefs.mode))
                openChat(conversations.first().id)
            }
            store.save(conversations)
            val ok = withContext(Dispatchers.IO) { client().push(store.toPushJson(conversations, tombs)) }
            syncing = false; online = ok; lastSync = nowHm()
            onStatus(if (ok) "" else "⚠️ Push failed")
        }
    }

    /** Incoming delete tombstones (JSON {id: deletedAt}) — even without new chats. */
    fun applyTombstones(json: String) {
        try {
            val o = JSONObject(json)
            var changed = false
            o.keys().forEach { id ->
                val dat = o.optLong(id)
                if ((tombs[id] ?: 0) < dat) tombs[id] = dat
                val idx = conversations.indexOfFirst { it.id == id }
                if (idx >= 0 && conversations[idx].updatedAt <= dat) {
                    if (currentId == conversations[idx].id)
                        openChat(conversations.firstOrNull { it.id != id }?.id ?: currentId)
                    conversations.removeAt(idx); changed = true
                }
            }
            store.saveTombs(tombs)
            if (changed) {
                if (conversations.isEmpty()) conversations.add(Conversation(mode = prefs.mode))
                if (conversations.none { it.id == currentId }) openChat(conversations.first().id)
                store.save(conversations)
            }
        } catch (_: Exception) {}
    }

    /** One full reconcile, then the long-poll for good (/api/chats?since=&wait=):
     *  what the web or another device writes appears within a second. While a
     *  reply streams, the open chat is not merged (ChatMerge busyId). */
    suspend fun runLiveSync() {
        while (prefs.serverUrl.isBlank()) delay(3000)
        sync()
        while (true) {
            val res = withContext(Dispatchers.IO) { client().pollChats(chatsRev, 25) }
            if (res == null) { online = false; delay(5000); continue }   // offline / old manager
            online = true
            chatsRev = res.rev
            res.tombstones?.let { applyTombstones(it) }
            val remote = res.chats ?: continue                            // timeout, nothing new
            var waited = 0
            while (busy && waited++ < 120) delay(500)
            val (merged, changed) = ChatMerge.merge(conversations, store.fromJson(remote), tombs,
                                                    busyId = if (busy) currentId else null)
            if (!changed) continue
            lastSync = nowHm()
            conversations.clear(); conversations.addAll(merged)
            if (conversations.isNotEmpty() && conversations.none { it.id == currentId })
                openChat(conversations.first().id)
            store.save(conversations)                                     // local only, no push
        }
    }

    /** Replies whose stream broke (app closed, network lost) are filled in from
     *  the manager by their turn id (/api/trace/<inst>?turn=). Only empty and
     *  transport-error bubbles; an aborted reply stays aborted. */
    suspend fun recoverReplies() {
        if (prefs.serverUrl.isBlank()) return
        val now = System.currentTimeMillis()
        for (c in conversations.toList()) {
            if (c.mode != "server") continue
            val inst = c.instance.ifBlank { prefs.instance }
            if (inst.isBlank()) continue
            for (m in c.messages.toList()) {
                val t = m.turn ?: continue
                if (m.user || !ServerAgent.needsRecovery(m.text)) continue
                if (busy && c.id == currentId && m.key == c.messages.lastOrNull()?.key) continue   // streaming now
                val first = recoverTried.getOrPut(t) { now }
                if (now - first > 30 * 60_000L) continue                  // gave up on this one
                val tr = withContext(Dispatchers.IO) { client().trace(inst, t) } ?: continue
                val r = ServerAgent.recovered(tr)
                when (r.state) {
                    "unknown" -> { if (now - first > 2 * 60_000L) recoverTried[t] = 0L; continue }
                    "running" -> continue
                    "none" -> { recoverTried[t] = 0L; continue }
                }
                val i = c.messages.indexOfFirst { it.key == m.key }
                if (i >= 0 && ServerAgent.needsRecovery(c.messages[i].text)) {
                    c.messages[i] = c.messages[i].copy(text = r.answer)
                    c.updatedAt = System.currentTimeMillis()
                    store.save(conversations)
                    pushChats()
                }
            }
        }
    }

    suspend fun runRecovery() {
        delay(2000)
        while (true) { runCatching { recoverReplies() }; delay(10_000) }
    }

    // ---- turns --------------------------------------------------------------------
    /**
     * Send [text] (what the model gets) and show [shown] as the user's bubble.
     * The reply streams into a new bubble of the current chat; [onDone] gets its
     * index and text when the turn ended normally (not when it was cancelled).
     * Returns false when nothing was sent (busy, or a slash command the app handled).
     */
    fun send(text: String, shown: String, imgB64: String? = null, img: Bitmap? = null, useWeb: Boolean = false,
             onOpenAgents: () -> Unit = {}, onDone: (Int, String) -> Unit = { _, _ -> }): Boolean {
        if (busy) return false
        val conv = current
        val msgs = conv.messages
        msgs.add(Msg(true, shown, image = imgB64))
        if (shown.startsWith("/") && handleSlash(shown, msgs, onOpenAgents)) { persist(); return false }
        busy = true
        persist()
        val turnId = if (conv.mode == "server") ServerAgent.newTurnId() else null
        // the turn id is OURS and stored with the (still empty) bubble at once: if
        // the stream dies, recoverReplies() fills the reply in by it
        val botMsg = Msg(false, "", turn = turnId)
        msgs.add(botMsg)
        persist()
        val botKey = botMsg.key                  // address by key, NOT index (interrupt/sync-safe)
        fun update(f: (Msg) -> Msg) {
            val i = msgs.indexOfFirst { it.key == botKey }
            if (i >= 0) msgs[i] = f(msgs[i])
        }
        fun streamed(chunk: String) {
            update { it.copy(text = it.text + chunk) }
            val t = System.currentTimeMillis()
            if (t - lastStreamSave > 800) { lastStreamSave = t; conv.updatedAt = t; store.save(conversations) }
        }
        val myGen = ++turnGen
        fun finish() {
            busy = false; persist()
            val bi = msgs.indexOfFirst { it.key == botKey }
            if (bi >= 0) onDone(bi, msgs[bi].text)
        }
        if (turnId != null) {
            val inst = conv.instance.ifBlank { prefs.instance }
            val ch = ServerAgent.CancelHandle(); cancelHandle = ch
            scope.launch {
                val err = withContext(Dispatchers.IO) {
                    ServerAgent.chatStream(prefs.serverUrl, inst, prefs.user, prefs.pass, text, imgB64,
                        chatId = conv.id, turn = turnId, cancel = ch,
                        onTurn = { t -> post { update { it.copy(turn = t) } } }) { chunk ->
                        if (myGen != turnGen) return@chatStream
                        post { if (myGen == turnGen) streamed(chunk) }
                    }
                }
                if (myGen != turnGen) return@launch                  // aborted -> nothing more
                if (err != null) post { streamed("\n$err") }
                finish()
            }
        } else {
            scope.launch {
                try {
                    withContext(Dispatchers.IO) {
                        val g = gemma ?: throw IllegalStateException("no local model")
                        if (!g.isReady() && prefs.activeModel.isNotEmpty()) {
                            try { g.load(store.modelFile(prefs.activeModel).absolutePath) } catch (_: Exception) {}
                        }
                        val prompt = if (useWeb && img == null) {
                            post { onStatus("🌐 Web research…") }
                            val ctx = try { WebSearch.buildContext(text) } catch (e: Exception) { "" }
                            post { onStatus("") }
                            if (ctx.isNotBlank())
                                "Answer the following question using this current web information. " +
                                "Cite the source (URL) if possible.\n\n$ctx\n\nQuestion: $text"
                            else text
                        } else text
                        g.generateStreaming(prompt, img) { d -> post { streamed(d) } }
                    }
                } catch (e: Exception) {
                    post { update { it.copy(text = "⚠️ ${e.message}") } }
                } finally {
                    finish()
                }
            }
        }
        return true
    }

    /** Discard the running reply: the stream is dropped, late chunks ignored, an
     *  empty reply bubble is marked as aborted. */
    fun cancelTurn() {
        turnGen++
        runCatching { cancelHandle?.cancel() }
        val msgs = current.messages
        val li = msgs.lastIndex
        if (li >= 0 && !msgs[li].user && msgs[li].text.isBlank()) msgs[li] = msgs[li].copy(text = "_(aborted)_")
        busy = false
        persist()
    }

    /** true = the app handled the command (nothing to the agent); false = pass it
     *  on as normal text (so the agent's own commands like /reset work). */
    fun handleSlash(text: String, msgs: SnapshotStateList<Msg>, onOpenAgents: () -> Unit = {}): Boolean {
        val body = text.removePrefix("/").trim()
        val cmd = body.substringBefore(' ').lowercase()
        val rest = body.substringAfter(' ', "").trim()
        when (cmd) {
            "help", "" -> msgs.add(Msg(false,
                "App commands:\n" +
                "/task <text> – background task on the current agent\n" +
                "/task every 30m <text> – recurring (also: daily 08:00, hourly)\n" +
                "/agents – open agent management\n" +
                "/help – this help\n" +
                "Other /-commands (e.g. /reset) go to the agent."))
            "task" -> {
                val inst = current.instance.ifBlank { prefs.instance }
                if (inst.isBlank()) { msgs.add(Msg(false, "⚠️ No server agent selected (tap a chip above).")); return true }
                val m = Regex("^(every\\s+\\d+[mhd]|daily\\s+\\d{1,2}:\\d{2}|hourly)\\s+(.*)", RegexOption.IGNORE_CASE).find(rest)
                val schedule = m?.groupValues?.get(1)?.trim() ?: ""
                val message = (m?.groupValues?.get(2) ?: rest).trim()
                if (message.isBlank()) { msgs.add(Msg(false, "⚠️ Usage: /task <text>")); return true }
                scope.launch {
                    val mc = client()               // its own client: the error belongs to this call
                    val r = withContext(Dispatchers.IO) { mc.createTask(inst, message, schedule) }
                    msgs.add(Msg(false, if (r != null)
                        "✅ Task created on @$inst${if (schedule.isNotBlank()) " ($schedule)" else " (background)"}. Drawer → Tasks."
                        else "⚠️ ${mc.lastError}"))
                    persist()
                }
            }
            "agents" -> { msgs.add(Msg(false, "Opening server agents…")); onOpenAgents() }
            // /login is moot: claudy signs in at boot via the host
            "login" -> msgs.add(Msg(false, "No login needed – the agent is signed in via the host."))
            else -> return false
        }
        return true
    }
}
