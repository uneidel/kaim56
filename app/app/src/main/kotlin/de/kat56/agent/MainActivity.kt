package de.kat56.agent

import android.Manifest
import androidx.core.content.ContextCompat
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.media.MediaPlayer
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.HourglassEmpty
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.VolumeUp
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.DeleteSweep
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.PhotoCamera
import androidx.compose.material.icons.outlined.PhotoLibrary
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        val gemma = LocalGemma(this)
        val store = ChatStore(this)
        store.migrate(prefs)   // v1.0-Modell uebernehmen, falls vorhanden
        setContent {
            // Der Prototyp ist nur in einem Band gezeichnet (dark="true"),
            // deshalb kein isSystemInDarkTheme() mehr.
            MaterialTheme(
                colorScheme = KatColors,
                typography = KatTypography,
                shapes = KatShapes,
            ) {
                KatAgentApp(prefs, gemma, store)
            }
        }
    }
}

/** Ein Modell-Vorschlag aus dem Prototyp (Name, Beschreibung, Marke, Groesse, URL). */
private data class Preset(
    val name: String, val desc: String, val tag: String, val size: String, val url: String,
)

private val PRESETS = listOf(
    Preset("Gemma-4 E4B", "Text · Bild · Audio, beste Qualität", "multimodal", "3,7 GB",
        "https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm"),
    Preset("Gemma-4 E2B", "Text · Bild · Audio, sparsamer", "multimodal", "2,6 GB",
        "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm"),
    Preset("Gemma-3n E2B", "Bewährt, int4-quantisiert", "text", "3,0 GB",
        "https://huggingface.co/litert-community/Gemma-3n-E2B-it-litert-lm/resolve/main/gemma-3n-E2B-it-int4.litertlm"),
    Preset("Qwen2.5 1.5B", "Schnell, gut für einfache Aufgaben", "text", "1,6 GB",
        "https://huggingface.co/litert-community/Qwen2.5-1.5B-Instruct/resolve/main/Qwen2.5-1.5B-Instruct_multi-prefill-seq_q8_ekv1280.litertlm"),
    Preset("Qwen2 0.5B", "Minimal, läuft auf schwacher Hardware", "klein", "0,6 GB",
        "https://huggingface.co/litert-community/Qwen2-0.5B-Instruct/resolve/main/Qwen2-0.5B-Instruct_multi-prefill-seq_q8_ekv1280.litertlm"),
)

private fun nowHm(): String = SimpleDateFormat("HH:mm", Locale.GERMANY).format(Date())

/** Kurzname des Agenten eines Chats — im Prototyp die Zeile unter dem Titel. */
private fun agentOf(c: Conversation): String =
    if (c.mode == "server") c.instance.ifBlank { "server" } else "device"

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun KatAgentApp(prefs: Prefs, gemma: LocalGemma, store: ChatStore) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val mainHandler = remember { Handler(Looper.getMainLooper()) }

    val conversations = remember { mutableStateListOf<Conversation>().also { it.addAll(store.load()) } }
    if (conversations.isEmpty()) conversations.add(Conversation(mode = prefs.mode))
    var currentId by remember {
        mutableStateOf(prefs.currentChatId.takeIf { id -> conversations.any { it.id == id } } ?: conversations.first().id)
    }
    LaunchedEffect(currentId) { prefs.currentChatId = currentId }
    val current = conversations.firstOrNull { it.id == currentId } ?: conversations.first()
    val lastStreamSave = remember { longArrayOf(0L) }

    val drawerState = rememberDrawerState(DrawerValue.Closed)
    var input by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    // Der Prototyp kennt drei Vollbilder: Chat, Tasks, Settings.
    var screen by remember { mutableStateOf<String?>(null) }
    var showAgents by remember { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
    var attachOpen by remember { mutableStateOf(false) }
    var pendingImage by remember { mutableStateOf<Bitmap?>(null) }
    var web by remember { mutableStateOf(prefs.webAccess) }
    var instances by remember { mutableStateOf<List<AgentInstance>>(emptyList()) }
    // Kopfzeile und Einstellungen zeigen den Sync-Zustand ("Syncing …" / "Synced · N chats").
    var syncing by remember { mutableStateOf(false) }
    var lastSync by remember { mutableStateOf("") }
    var online by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    fun loadInstances() {
        if (prefs.serverUrl.isBlank()) return
        scope.launch {
            val j = withContext(Dispatchers.IO) { ManagerSync.listInstances(prefs.serverUrl, prefs.user, prefs.pass) }
            if (j != null) instances = ManagerSync.parseInstances(j)
        }
    }
    // Beim Start laden und nach dem Schliessen der Agenten-Verwaltung erneut.
    LaunchedEffect(showAgents) { if (!showAgents) loadInstances() }
    // Der Prototyp hat keinen Aktualisieren-Knopf mehr in der Chip-Zeile; die
    // Liste wird stattdessen beim Oeffnen der Schublade nachgezogen.
    LaunchedEffect(drawerState.currentValue) {
        if (drawerState.currentValue == DrawerValue.Open) loadInstances()
    }

    val notifPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(Unit) { if (Build.VERSION.SDK_INT >= 33) notifPerm.launch(Manifest.permission.POST_NOTIFICATIONS) }

    // Hintergrund-Download beobachten.
    val dl by DownloadBus.state.collectAsState()
    LaunchedEffect(dl) {
        when (val s = dl) {
            is Dl.Progress -> status = if (s.total > 0)
                "Download ${s.done * 100 / s.total}% (${s.done / 1_000_000}/${s.total / 1_000_000} MB)"
                else "Download ${s.done / 1_000_000} MB…"
            is Dl.Done -> {
                status = "Modell wird geladen…"
                withContext(Dispatchers.IO) { try { gemma.load(s.path) } catch (e: Exception) { status = "⚠️ ${e.message}" } }
                if (gemma.isReady()) status = "Modell geladen ✅"
            }
            is Dl.Error -> status = "⚠️ ${s.msg}"
            Dl.Idle -> {}
        }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) scope.launch {
            status = "Modell wird kopiert…"
            val path = withContext(Dispatchers.IO) { copyModel(context, store.modelsDir, uri) }
            if (path != null) {
                prefs.activeModel = File(path).name
                status = "Modell wird geladen…"
                withContext(Dispatchers.IO) { try { gemma.load(path) } catch (e: Exception) { status = "⚠️ ${e.message}" } }
                if (gemma.isReady()) status = "Modell geladen ✅"
            } else status = "⚠️ Kopieren fehlgeschlagen"
        }
    }
    val imagePicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
        if (uri != null) scope.launch {
            val bmp = withContext(Dispatchers.IO) { loadBitmap(context, uri) }
            if (bmp != null) pendingImage = bmp else status = "⚠️ Bild konnte nicht geladen werden"
        }
    }
    // Anhang-Blatt des Prototyps: "Camera" nimmt eine Vorschau auf (kein
    // FileProvider noetig), send() verarbeitet ohnehin nur Bitmaps.
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicturePreview()) { bmp: Bitmap? ->
        if (bmp != null) pendingImage = bmp
    }

    // Debounced Hintergrund-Push zum Manager, damit neue/geaenderte Chats live
    // beim Server (und darueber in der Web-UI) landen — nicht erst beim naechsten
    // manuellen Sync. Der Manager MERGED serverseitig, ueberschreibt also nichts.
    val pushJob = remember { arrayOfNulls<kotlinx.coroutines.Job>(1) }
    fun pushChats() {
        if (prefs.serverUrl.isBlank()) return
        pushJob[0]?.cancel()
        pushJob[0] = scope.launch {
            delay(1200)
            withContext(Dispatchers.IO) {
                ManagerSync.push(prefs.serverUrl, prefs.user, prefs.pass, store.toJson(conversations))
            }
        }
    }

    fun persist() {
        current.updatedAt = System.currentTimeMillis()
        if (current.title == "Neuer Chat") {
            current.messages.firstOrNull { it.user }?.text?.trim()?.take(40)?.let {
                if (it.isNotBlank()) current.title = it
            }
        }
        store.save(conversations)
        pushChats()
    }

    fun newChat() {
        val c = Conversation(mode = prefs.mode)
        conversations.add(0, c)
        currentId = c.id
        store.save(conversations)
    }

    // Agent wechseln = zum Verlauf DIESES Agenten springen (ein Thread pro Agent),
    // statt den offenen Chat still umzuhaengen. Ist der aktuelle Chat noch leer,
    // wird er einfach zugewiesen (kein neuer leerer Thread).
    fun switchToAgent(mode: String, instance: String) {
        prefs.mode = mode; prefs.instance = instance
        if (current.messages.isEmpty()) {
            current.mode = mode; current.instance = instance; persist(); return
        }
        val existing = conversations
            .filter { it.mode == mode && (mode == "local" || it.instance == instance) }
            .maxByOrNull { it.updatedAt }
        currentId = existing?.id ?: Conversation(mode = mode, instance = instance)
            .also { conversations.add(0, it); store.save(conversations) }.id
    }

    fun deleteChat(c: Conversation) {
        conversations.remove(c)
        if (conversations.isEmpty()) conversations.add(Conversation(mode = prefs.mode))
        if (currentId == c.id) currentId = conversations.first().id
        store.save(conversations)
    }

    fun selectModel(f: File) {
        prefs.activeModel = f.name
        status = "Modell wird geladen…"
        scope.launch {
            withContext(Dispatchers.IO) { try { gemma.load(f.absolutePath) } catch (e: Exception) { status = "⚠️ ${e.message}" } }
            if (gemma.isReady()) status = "Modell geladen ✅"
        }
    }

    fun sync() {
        if (prefs.serverUrl.isBlank()) { status = "⚠️ Server-URL fehlt (Einstellungen)"; return }
        scope.launch {
            syncing = true
            status = "Sync…"
            val remoteJson = withContext(Dispatchers.IO) { ManagerSync.pull(prefs.serverUrl, prefs.user, prefs.pass) }
            if (remoteJson == null) {
                syncing = false; online = false
                status = "⚠️ Sync: Server nicht erreichbar"; return@launch
            }
            val byId = LinkedHashMap<String, Conversation>()
            for (c in conversations) byId[c.id] = c
            for (r in store.fromJson(remoteJson)) {
                val local = byId[r.id]
                if (local == null || r.updatedAt > local.updatedAt) byId[r.id] = r
            }
            val merged = byId.values.sortedByDescending { it.updatedAt }
            conversations.clear(); conversations.addAll(merged)
            if (conversations.none { it.id == currentId }) {
                if (conversations.isEmpty()) conversations.add(Conversation(mode = prefs.mode))
                currentId = conversations.first().id
            }
            store.save(conversations)
            val ok = withContext(Dispatchers.IO) { ManagerSync.push(prefs.serverUrl, prefs.user, prefs.pass, store.toJson(conversations)) }
            syncing = false; online = ok; lastSync = nowHm()
            status = if (ok) "" else "⚠️ Push fehlgeschlagen"
        }
    }

    // Live-Sync mit dem Manager: einmal voll abgleichen, danach dauerhaft am
    // Long-Poll haengen (/api/chats?since=&wait=). Der Manager antwortet, sobald
    // die Web-UI oder ein anderes Geraet schreibt — neue Nachrichten stehen also
    // binnen Sekundenbruchteilen hier, ohne Dauer-Polling und ohne Neustart.
    // Waehrend eine Antwort streamt (busy) wird nicht gemerged, sonst wuerde der
    // Teiltext ueberschrieben.
    val chatsRev = remember { longArrayOf(0L) }
    LaunchedEffect(Unit) {
        while (prefs.serverUrl.isBlank()) delay(3000)
        sync()
        while (true) {
            val res = withContext(Dispatchers.IO) {
                ManagerSync.pollChats(prefs.serverUrl, prefs.user, prefs.pass, chatsRev[0], 25)
            }
            if (res == null) { online = false; delay(5000); continue }   // offline / alter Manager
            online = true
            chatsRev[0] = res.rev
            val remote = res.chats ?: continue           // Zeitablauf, nichts Neues
            var waited = 0
            while (busy && waited++ < 120) delay(500)
            // WICHTIG: bestehende Conversation-Objekte werden BEFUELLT, nicht
            // ersetzt. send() haelt eine Referenz auf current.messages fest und
            // streamt die Antwort dorthin — tauscht man das Objekt aus, landen
            // Frage und Antwort in einer abgehaengten Liste: unsichtbar,
            // ungespeichert, nie gepusht. Die offene Konversation bleibt
            // zusaetzlich unangetastet, solange ein Turn laeuft.
            val byId = LinkedHashMap<String, Conversation>()
            for (c in conversations) byId[c.id] = c
            var changed = false
            for (r in store.fromJson(remote)) {
                val local = byId[r.id]
                if (local == null) {                        // wirklich neu
                    byId[r.id] = r; changed = true
                    continue
                }
                if (r.updatedAt <= local.updatedAt) continue   // lokal ist aktueller
                if (busy && local.id == currentId) continue    // laufender Turn
                // Nachrichten nur ANHAENGEN. Ein Ersetzen wuerde eine gerade
                // getippte, noch nicht gepushte Frage wegwischen - genau der
                // Fall, in dem die Gegenseite (Web/Manager) eine neuere Uhr
                // hat. Nur wenn die lokale Liste ein Praefix der entfernten
                // ist, sind wir sicher, dass nichts Eigenes verlorengeht;
                // sonst gleicht der naechste Push das aus.
                val lm = local.messages
                val rm = r.messages
                if (rm.size < lm.size || lm.indices.any { lm[it] != rm[it] }) continue
                if (rm.size > lm.size) {
                    for (i in lm.size until rm.size) lm.add(rm[i])
                    changed = true
                }
                if (local.title != r.title && r.title.isNotBlank()) { local.title = r.title; changed = true }
                if (local.instance != r.instance && r.instance.isNotBlank()) local.instance = r.instance
                local.updatedAt = r.updatedAt
            }
            if (!changed) continue
            lastSync = nowHm()
            val merged = byId.values.sortedByDescending { it.updatedAt }
            conversations.clear()
            conversations.addAll(merged)
            if (conversations.isNotEmpty() && conversations.none { it.id == currentId })
                currentId = conversations.first().id
            store.save(conversations)                    // nur lokal, kein Push
        }
    }

    fun handleSlash(text: String, msgs: androidx.compose.runtime.snapshots.SnapshotStateList<Msg>) {
        val body = text.removePrefix("/").trim()
        val cmd = body.substringBefore(' ').lowercase()
        val rest = body.substringAfter(' ', "").trim()
        when (cmd) {
            "help", "" -> msgs.add(Msg(false,
                "Befehle:\n" +
                "/task <text> – Hintergrundaufgabe auf dem aktuellen Agenten\n" +
                "/task every 30m <text> – wiederkehrend (auch: daily 08:00, hourly)\n" +
                "/agents – Agenten-Verwaltung öffnen\n" +
                "/help – diese Hilfe"))
            "task" -> {
                val inst = current.instance.ifBlank { prefs.instance }
                if (inst.isBlank()) { msgs.add(Msg(false, "⚠️ Kein Server-Agent gewählt (oben einen Chip antippen).")); return }
                val m = Regex("^(every\\s+\\d+[mhd]|daily\\s+\\d{1,2}:\\d{2}|hourly)\\s+(.*)", RegexOption.IGNORE_CASE).find(rest)
                val schedule = m?.groupValues?.get(1)?.trim() ?: ""
                val message = (m?.groupValues?.get(2) ?: rest).trim()
                if (message.isBlank()) { msgs.add(Msg(false, "⚠️ Nutzung: /task <text>")); return }
                scope.launch {
                    val r = withContext(Dispatchers.IO) { ManagerSync.createTask(prefs.serverUrl, prefs.user, prefs.pass, inst, message, schedule) }
                    msgs.add(Msg(false, if (r != null)
                        "✅ Aufgabe auf @$inst angelegt${if (schedule.isNotBlank()) " ($schedule)" else " (Hintergrund)"}. Schublade → Tasks."
                        else "⚠️ ${ManagerSync.lastStatus}"))
                    persist()
                }
            }
            "agents" -> { msgs.add(Msg(false, "Öffne Server-Agenten…")); showAgents = true }
            else -> msgs.add(Msg(false, "Unbekannter Befehl /$cmd — /help für Hilfe."))
        }
    }

    // ── Sprachbedienung ────────────────────────────────────────────────────
    // Aufnehmen im Geraet, Erkennen und Sprechen im Manager (Parakeet/Piper).
    // Vorgelesen wird nur, was per Sprache gefragt wurde — eine lange
    // Erklaerung ungefragt vorzulesen waere eine Zumutung.
    var recording by remember { mutableStateOf(false) }
    var transcribing by remember { mutableStateOf(false) }
    var voiceIn by remember { mutableStateOf(false) }
    // send() ist weiter unten deklariert; eine lokale Funktion darf man in
    // Kotlin nicht vorher aufrufen. Der Merker ueberbrueckt das.
    var pendingVoiceSend by remember { mutableStateOf(false) }
    val recorder = remember { arrayOfNulls<MediaRecorder>(1) }
    val recFile = remember { arrayOfNulls<java.io.File>(1) }
    val player = remember { arrayOfNulls<MediaPlayer>(1) }

    fun speakText(text: String) {
        if (text.isBlank() || prefs.serverUrl.isBlank()) return
        scope.launch {
            val wav = withContext(Dispatchers.IO) {
                ManagerSync.tts(prefs.serverUrl, prefs.user, prefs.pass, text.take(4000))
            }
            if (wav == null) { status = "⚠️ Vorlesen: ${ManagerSync.lastStatus}"; return@launch }
            withContext(Dispatchers.IO) {
                runCatching {
                    val f = java.io.File(context.cacheDir, "speak.wav")
                    f.writeBytes(wav)
                    player[0]?.release()
                    player[0] = MediaPlayer().apply {
                        setDataSource(f.absolutePath)
                        setOnCompletionListener { mp -> mp.release(); player[0] = null }
                        prepare(); start()
                    }
                }
            }
        }
    }

    fun stopRec() {
        val r = recorder[0] ?: return
        recorder[0] = null; recording = false
        // stop() wirft, wenn zu frueh gestoppt wurde (zu kurze Aufnahme) —
        // dann gibt es schlicht nichts zu erkennen.
        val ok = runCatching { r.stop() }.isSuccess
        runCatching { r.release() }
        val f = recFile[0]; recFile[0] = null
        if (!ok || f == null || !f.exists() || f.length() < 2000) {
            status = "Zu kurz — nochmal halten"; f?.delete(); return
        }
        transcribing = true
        scope.launch {
            val text = withContext(Dispatchers.IO) {
                val bytes = f.readBytes(); f.delete()
                ManagerSync.stt(prefs.serverUrl, prefs.user, prefs.pass, bytes, "audio/mp4")
            }
            transcribing = false
            if (text.isNullOrBlank()) { status = "Nichts verstanden (${ManagerSync.lastStatus})"; return@launch }
            input = text
            voiceIn = true
            pendingVoiceSend = true      // freihaendig: gleich abschicken
        }
    }

    fun startRec() {
        if (prefs.serverUrl.isBlank()) { status = "⚠️ Server-URL fehlt (Einstellungen)"; return }
        val f = java.io.File(context.cacheDir, "rec.m4a")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
        val ok = runCatching {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioSamplingRate(16000)      // mehr braucht die Erkennung nicht
            r.setAudioChannels(1)
            r.setAudioEncodingBitRate(32000)
            r.setOutputFile(f.absolutePath)
            r.prepare(); r.start()
        }.isSuccess
        if (!ok) { runCatching { r.release() }; status = "⚠️ Aufnahme nicht möglich"; return }
        recorder[0] = r; recFile[0] = f; recording = true
        status = "Aufnahme… nochmal tippen zum Beenden"
    }

    val micPerm = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startRec() else status = "⚠️ Ohne Mikrofonfreigabe geht es nicht"
    }

    fun micToggle() {
        if (recording) { stopRec(); return }
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) startRec() else micPerm.launch(Manifest.permission.RECORD_AUDIO)
    }

    fun send() {
        val text = input.trim()
        if ((text.isEmpty() && pendingImage == null) || busy) return
        val img = pendingImage
        val msgs = current.messages
        msgs.add(Msg(true, if (img != null) "📷 " + (if (text.isEmpty()) "(Bild)" else text) else text))
        input = ""; pendingImage = null
        if (text.startsWith("/")) { handleSlash(text, msgs); persist(); return }
        busy = true
        persist()

        if (current.mode == "server") {
            msgs.add(Msg(false, ""))
            val idx = msgs.lastIndex
            val inst = current.instance.ifBlank { prefs.instance }
            val imgB64 = img?.let { bitmapToBase64(it) }
            scope.launch {
                val err = withContext(Dispatchers.IO) {
                    ServerAgent.chatStream(prefs.serverUrl, inst, prefs.user, prefs.pass, text, imgB64) { chunk ->
                        mainHandler.post {
                            if (idx < msgs.size) {
                                msgs[idx] = msgs[idx].copy(text = msgs[idx].text + chunk)
                                val t = System.currentTimeMillis()
                                if (t - lastStreamSave[0] > 800) { lastStreamSave[0] = t; current.updatedAt = t; store.save(conversations) }
                            }
                        }
                    }
                }
                if (err != null) mainHandler.post { if (idx < msgs.size) msgs[idx] = msgs[idx].copy(text = msgs[idx].text + "\n$err") }
                busy = false; persist(); listState.animateScrollToItem(msgs.size)
                if (voiceIn) { voiceIn = false; speakText(msgs.getOrNull(idx)?.text ?: "") }
            }
        } else {
            msgs.add(Msg(false, ""))
            val idx = msgs.lastIndex
            val useWeb = web
            scope.launch {
                try {
                    withContext(Dispatchers.IO) {
                        if (!gemma.isReady() && prefs.activeModel.isNotEmpty()) {
                            try { gemma.load(store.modelFile(prefs.activeModel).absolutePath) } catch (_: Exception) {}
                        }
                        val prompt = if (useWeb && img == null) {
                            mainHandler.post { status = "🌐 Web-Recherche…" }
                            val ctx = try { WebSearch.buildContext(text) } catch (e: Exception) { "" }
                            mainHandler.post { status = "" }
                            if (ctx.isNotBlank())
                                "Beantworte die folgende Frage mit Hilfe dieser aktuellen Web-Informationen. " +
                                "Nenne die Quelle (URL), wenn möglich.\n\n$ctx\n\nFrage: $text"
                            else text
                        } else text
                        gemma.generateStreaming(prompt, img) { d ->
                            mainHandler.post {
                                if (idx < msgs.size) {
                                    msgs[idx] = msgs[idx].copy(text = msgs[idx].text + d)
                                    val t = System.currentTimeMillis()
                                    if (t - lastStreamSave[0] > 800) { lastStreamSave[0] = t; current.updatedAt = t; store.save(conversations) }
                                }
                            }
                        }
                    }
                } catch (e: Exception) {
                    mainHandler.post { if (idx < msgs.size) msgs[idx] = msgs[idx].copy(text = "⚠️ ${e.message}") }
                } finally {
                    busy = false; persist(); listState.animateScrollToItem(msgs.size)
                if (voiceIn) { voiceIn = false; speakText(msgs.getOrNull(idx)?.text ?: "") }
                }
            }
        }
    }

    // Wie im Prototyp (componentDidUpdate): die Liste haengt am unteren Rand.
    LaunchedEffect(current.messages.size, currentId) {
        if (current.messages.isNotEmpty()) listState.animateScrollToItem(current.messages.size)
    }

    // ── abgeleitete Beschriftungen ──────────────────────────────────────────
    LaunchedEffect(pendingVoiceSend) {
        if (pendingVoiceSend) { pendingVoiceSend = false; send() }
    }

    val serverModel = instances.firstOrNull { it.name == current.instance }?.model ?: ""
    val modelLabel = if (current.mode == "server")
        serverModel.ifBlank { current.instance.ifBlank { "server" } }
    else prefs.activeModel.ifBlank { "kein Modell geladen" }
    val agentLabel = agentOf(current)
    val syncLabel = if (syncing) "Syncing …" else "Synced · ${conversations.size} chats"

    BackHandler(screen != null || menuOpen || attachOpen) {
        when {
            menuOpen -> menuOpen = false
            attachOpen -> attachOpen = false
            else -> screen = null
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        scrimColor = Kat.scrim,
        drawerContent = {
            KatDrawer(
                conversations.sortedByDescending { it.updatedAt },
                currentId,
                agentCount = instances.size,
                online = online,
                onSelect = { currentId = it; scope.launch { drawerState.close() } },
                onNew = { newChat(); scope.launch { drawerState.close() } },
                onDelete = { deleteChat(it) },
                onTasks = { screen = "tasks"; scope.launch { drawerState.close() } },
                onSettings = { screen = "settings"; scope.launch { drawerState.close() } },
            )
        }
    ) {
        Box(Modifier.fillMaxSize().background(Kat.bg)) {
            Column(Modifier.fillMaxSize()) {
                // ── Kopf: Menue, Titel + Sync-Zeile, Ueberlaufmenue ─────────
                Row(
                    Modifier.fillMaxWidth().padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 4.dp),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RoundIconButton({ scope.launch { drawerState.open() } }) {
                        Icon(Icons.Filled.Menu, "Chats", Modifier.size(20.dp), tint = Kat.textDim)
                    }
                    Column(Modifier.weight(1f).padding(start = 4.dp)) {
                        Text(
                            current.title, style = MaterialTheme.typography.titleMedium,
                            color = Kat.text, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(Modifier.size(6.dp).clip(CircleShape)
                                .background(if (online) Kat.green else Kat.textGhost))
                            Text(
                                syncLabel, fontSize = 12.sp, fontFamily = Plex, color = Kat.textFaint,
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                    RoundIconButton(
                        { menuOpen = !menuOpen },
                        background = if (menuOpen) Kat.hover else Color.Transparent,
                    ) { Icon(Icons.Filled.MoreVert, "Menü", Modifier.size(18.dp), tint = Kat.textDim) }
                }

                // ── Agenten-Chips ───────────────────────────────────────────
                val chips = if (instances.isNotEmpty()) instances
                    else if (prefs.instance.isNotBlank()) listOf(AgentInstance(prefs.instance, false, "", "", "")) else emptyList()
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                        .padding(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    AgentChip("Device", current.mode == "local", { switchToAgent("local", "") }) {
                        Icon(Icons.Filled.PhoneAndroid, null, Modifier.size(13.dp), tint = it)
                    }
                    chips.forEach { inst ->
                        AgentChip(
                            inst.name,
                            current.mode == "server" && current.instance == inst.name,
                            { switchToAgent("server", inst.name) },
                        ) {
                            Icon(
                                if (inst.running) Icons.Filled.Cloud else Icons.Outlined.CloudOff,
                                null, Modifier.size(14.dp), tint = it,
                            )
                        }
                    }
                }
                Hairline()

                // Rueckmeldungen (Download, Modell-Ladefehler, Sync-Probleme).
                // Steht so nicht im Prototyp, ist aber die einzige Stelle, an der
                // diese Meldungen ueberhaupt sichtbar werden.
                if (status.isNotEmpty()) Text(
                    status,
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                    fontSize = 12.sp, fontFamily = Plex,
                    color = if (status.startsWith("⚠️")) Kat.red else Kat.accentText,
                    maxLines = 2, overflow = TextOverflow.Ellipsis,
                )

                // ── Nachrichten ─────────────────────────────────────────────
                val bubbleMax = (LocalConfiguration.current.screenWidthDp * 0.78f).dp
                LazyColumn(
                    state = listState,
                    modifier = Modifier.weight(1f).fillMaxWidth(),
                    contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    item {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                            Box(
                                Modifier
                                    .clip(RoundedCornerShape(14.dp))
                                    .background(Kat.wash)
                                    .border(1.dp, Kat.hairline, RoundedCornerShape(14.dp))
                                    .padding(horizontal = 12.dp, vertical = 5.dp)
                            ) {
                                Text(
                                    modelLabel, fontFamily = PlexMono, fontSize = 11.5.sp,
                                    color = Kat.textFaint, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                    if (current.messages.isEmpty()) {
                        item { EmptyState(agentLabel, current.mode, Modifier.fillParentMaxHeight(0.72f)) }
                    } else {
                        items(current.messages) { m -> Bubble(m, agentLabel, current.mode, bubbleMax) { t -> speakText(t) } }
                    }
                }

                // ── Anhang-Vorschau (nicht im Prototyp, sonst waere der
                //    angehaengte Schnappschuss unsichtbar) ───────────────────
                pendingImage?.let { bmp ->
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Image(
                            bmp.asImageBitmap(), "Anhang",
                            Modifier.size(40.dp).clip(RoundedCornerShape(8.dp)),
                            contentScale = ContentScale.Crop,
                        )
                        Text("Bild angehängt", fontSize = 13.sp, fontFamily = Plex,
                            color = Kat.textDim, modifier = Modifier.weight(1f))
                        RoundIconButton({ pendingImage = null }, size = 32.dp) {
                            Icon(Icons.Filled.Close, "Entfernen", Modifier.size(16.dp), tint = Kat.textSubtle)
                        }
                    }
                }

                // ── Eingabezeile ────────────────────────────────────────────
                Hairline()
                Row(
                    Modifier.fillMaxWidth().background(Kat.bg)
                        .padding(start = 12.dp, end = 12.dp, top = 10.dp, bottom = 14.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RoundIconButton({ attachOpen = true }, enabled = !busy) {
                        Icon(Icons.Filled.Add, "Anhängen", Modifier.size(20.dp), tint = Kat.textMuted)
                    }
                    Box(
                        Modifier
                            .weight(1f)
                            .height(44.dp)
                            .clip(RoundedCornerShape(22.dp))
                            .background(Kat.surface)
                            .border(1.dp, Kat.border, RoundedCornerShape(22.dp))
                            .padding(horizontal = 18.dp),
                        contentAlignment = Alignment.CenterStart,
                    ) {
                        val style = TextStyle(fontFamily = Plex, fontSize = 15.sp, color = Kat.text)
                        BasicTextField(
                            value = input,
                            onValueChange = { input = it },
                            textStyle = style,
                            singleLine = true,
                            cursorBrush = SolidColor(Kat.accentText),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                            keyboardActions = KeyboardActions(onSend = { send() }),
                            modifier = Modifier.fillMaxWidth(),
                            decorationBox = { inner ->
                                if (input.isEmpty())
                                    Text("Message …", style = style.copy(color = Kat.textSubtle), maxLines = 1)
                                inner()
                            },
                        )
                    }
                    RoundIconButton(
                        { micToggle() }, enabled = !busy && !transcribing,
                        background = if (recording) Kat.accent else Kat.tile,
                    ) {
                        Icon(
                            if (transcribing) Icons.Filled.HourglassEmpty else Icons.Filled.Mic,
                            if (recording) "Aufnahme beenden" else "Sprechen",
                            Modifier.size(18.dp),
                            tint = if (recording) Kat.onAccent else Kat.textMuted,
                        )
                    }
                    val canSend = !busy && (input.isNotBlank() || pendingImage != null)
                    RoundIconButton(
                        { send() }, enabled = canSend,
                        background = if (canSend) Kat.accent else Kat.tile,
                    ) {
                        Icon(
                            Icons.AutoMirrored.Filled.Send, "Senden", Modifier.size(18.dp),
                            tint = if (canSend) Kat.onAccent else Kat.textSubtle,
                        )
                    }
                }
            }

            // ── Ueberlaufmenue ──────────────────────────────────────────────
            if (menuOpen) {
                Box(Modifier.fillMaxSize().tap { menuOpen = false })
                Column(
                    Modifier
                        .align(Alignment.TopEnd)
                        .padding(top = 56.dp, end = 10.dp)
                        .width(238.dp)
                        .clip(RoundedCornerShape(14.dp))
                        .background(Kat.surface)
                        .border(1.dp, Kat.border, RoundedCornerShape(14.dp))
                        .padding(6.dp),
                ) {
                    Kicker("Model", Modifier.padding(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 4.dp))
                    if (current.mode == "local") {
                        val models = remember(menuOpen) { store.models() }
                        if (models.isEmpty()) Text(
                            "Kein Modell geladen", Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
                            fontSize = 12.5.sp, fontFamily = PlexMono, color = Kat.textMuted,
                        )
                        models.forEach { f ->
                            MenuModelRow(f.name, f.name == prefs.activeModel) {
                                selectModel(f); menuOpen = false
                            }
                        }
                    } else {
                        // Das Modell eines Server-Agenten setzt der Manager, nicht
                        // die App — die Zeile zeigt es, schaltet aber nicht um.
                        MenuModelRow(modelLabel, true, null)
                    }
                    Box(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 6.dp)
                        .height(1.dp).background(Kat.hairlineStrong))
                    MenuRow("Clear conversation", Kat.textDim, Icons.Outlined.DeleteSweep) {
                        current.messages.clear(); menuOpen = false; persist()
                    }
                    MenuRow("Delete chat", Kat.red, Icons.Outlined.DeleteOutline) {
                        menuOpen = false; deleteChat(current)
                    }
                }
            }

            // ── Anhang-Blatt ────────────────────────────────────────────────
            AnimatedVisibility(attachOpen, enter = fadeIn(), exit = fadeOut()) {
                Box(Modifier.fillMaxSize().background(Kat.scrim).tap { attachOpen = false })
            }
            AnimatedVisibility(
                attachOpen,
                modifier = Modifier.align(Alignment.BottomCenter),
                enter = slideInVertically { it }, exit = slideOutVertically { it },
            ) {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp))
                        .background(Kat.elevated)
                        .padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 28.dp),
                ) {
                    Box(
                        Modifier.align(Alignment.CenterHorizontally).padding(bottom = 10.dp)
                            .size(36.dp, 4.dp).clip(RoundedCornerShape(2.dp))
                            .background(Color(0x26FFFFFF))
                    )
                    Kicker("Attach", Modifier.padding(start = 4.dp, bottom = 12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        AttachTile("Camera", Icons.Outlined.PhotoCamera, Modifier.weight(1f)) {
                            attachOpen = false; camera.launch(null)
                        }
                        AttachTile("Gallery", Icons.Outlined.PhotoLibrary, Modifier.weight(1f)) {
                            attachOpen = false; imagePicker.launch("image/*")
                        }
                    }
                }
            }

            // ── Vollbilder Tasks / Settings ─────────────────────────────────
            AnimatedVisibility(
                screen == "tasks",
                enter = slideInHorizontally { it }, exit = slideOutHorizontally { it },
            ) {
                TasksScreen(
                    prefs, instances,
                    onClose = { screen = null },
                    onStatus = { status = it },
                    onOpenChat = { instance ->
                        // Der Manager fuehrt je Instanz EINEN Aufgaben-Verlauf
                        // unter der festen id "task-<instanz>" (chat_log_append,
                        // kind="task"). Dieselbe id lokal verwenden, damit der
                        // Sync beide Seiten zusammenfuehrt statt zu doppeln.
                        val cid = "task-$instance"
                        if (conversations.none { it.id == cid }) {
                            conversations.add(0, Conversation(
                                id = cid, title = "Tasks · $instance",
                                mode = "server", instance = instance,
                                updatedAt = System.currentTimeMillis(),
                            ))
                            store.save(conversations)
                        }
                        currentId = cid
                        prefs.mode = "server"
                        prefs.instance = instance
                        screen = null
                    },
                )
            }
            AnimatedVisibility(
                screen == "settings",
                enter = slideInHorizontally { it }, exit = slideOutHorizontally { it },
            ) {
                SettingsScreen(
                    prefs, store, dl,
                    web = web, onWeb = { web = it; prefs.webAccess = it },
                    syncing = syncing, lastSync = lastSync, online = online,
                    onClose = { screen = null },
                    onSelectModel = { selectModel(it) },
                    onDeleteModel = { f -> f.delete(); if (prefs.activeModel == f.name) { prefs.activeModel = ""; gemma.close() } },
                    onPickModel = { picker.launch(arrayOf("application/octet-stream", "*/*")) },
                    onDownload = { url, token ->
                        DownloadService.start(context, url, token); status = "Download startet… (Hintergrund)"
                    },
                    onManageAgents = { showAgents = true },
                    onSync = { sync() },
                )
            }
        }
    }

    if (showAgents) {
        ServerAgentsDialog(prefs, onDismiss = { showAgents = false }, onStatus = { status = it })
    }
}

// ── Chat-Bausteine ──────────────────────────────────────────────────────────

@Composable
private fun AgentChip(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
    icon: @Composable (Color) -> Unit,
) {
    val fg = if (selected) Kat.accentBright else Kat.textMuted
    Row(
        Modifier
            .clip(RoundedCornerShape(18.dp))
            .background(if (selected) Kat.chipSel else Color.Transparent)
            .border(1.dp, if (selected) Kat.borderFocus else Kat.border, RoundedCornerShape(18.dp))
            .tap { onClick() }
            .padding(horizontal = 14.dp, vertical = 7.dp),
        horizontalArrangement = Arrangement.spacedBy(7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        icon(fg)
        Text(
            label, fontSize = 13.5.sp, fontFamily = Plex,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Medium,
            color = fg, maxLines = 1,
        )
    }
}

@Composable
private fun MenuModelRow(label: String, selected: Boolean, onClick: (() -> Unit)?) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(9.dp))
            .then(if (onClick != null) Modifier.tap { onClick() } else Modifier)
            .padding(horizontal = 12.dp, vertical = 9.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            label, Modifier.weight(1f), fontSize = 12.5.sp, fontFamily = PlexMono,
            color = if (selected) Kat.accentBright else Kat.textMuted,
            maxLines = 1, overflow = TextOverflow.Ellipsis,
        )
        if (selected) Icon(Icons.Filled.Check, null, Modifier.size(14.dp), tint = Kat.accentText)
    }
}

@Composable
private fun MenuRow(
    label: String,
    color: Color,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    onClick: () -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(9.dp))
            .tap { onClick() }
            .padding(horizontal = 12.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, Modifier.size(16.dp), tint = color)
        Text(label, fontSize = 14.sp, fontFamily = Plex, color = color, maxLines = 1)
    }
}

@Composable
private fun AttachTile(
    label: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    Column(
        modifier
            .clip(RoundedCornerShape(16.dp))
            .background(Kat.surface)
            .border(1.dp, Kat.border, RoundedCornerShape(16.dp))
            .tap { onClick() }
            .padding(horizontal = 8.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(icon, null, Modifier.size(24.dp), tint = Kat.accentText)
        Text(label, fontSize = 13.sp, fontFamily = Plex, fontWeight = FontWeight.Medium, color = Kat.textDim)
    }
}

/** 28-dp-Kachel links neben der Agenten-Nachricht. */
@Composable
private fun AgentAvatar(mode: String, size: androidx.compose.ui.unit.Dp = 28.dp) {
    Box(
        Modifier
            .size(size)
            .clip(RoundedCornerShape(size / 3.5f))
            .background(Kat.tile)
            .border(1.dp, Kat.tileBorder, RoundedCornerShape(size / 3.5f)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            if (mode == "server") Icons.Filled.Cloud else Icons.Filled.PhoneAndroid,
            null, Modifier.size(size / 2), tint = Kat.accentText,
        )
    }
}

@Composable
fun EmptyState(agent: String, mode: String, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
    ) {
        Box(
            Modifier.size(52.dp).clip(RoundedCornerShape(16.dp)).background(Kat.tile)
                .border(1.dp, Kat.tileBorder, RoundedCornerShape(16.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                if (mode == "server") Icons.Filled.Cloud else Icons.Filled.PhoneAndroid,
                null, Modifier.size(24.dp), tint = Kat.accentText,
            )
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            Text("New conversation with", fontSize = 14.sp, fontFamily = Plex, color = Kat.textSubtle)
            Text(agent, fontSize = 13.sp, fontFamily = PlexMono, color = Kat.accentText,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
fun Bubble(
    m: Msg, agentLabel: String, mode: String, maxWidth: androidx.compose.ui.unit.Dp,
    onSpeak: (String) -> Unit = {},
) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (m.user) Arrangement.End else Arrangement.Start,
        verticalAlignment = Alignment.Top,
    ) {
        if (!m.user) {
            AgentAvatar(mode)
            Spacer(Modifier.width(10.dp))
        }
        Column(
            horizontalAlignment = if (m.user) Alignment.End else Alignment.Start,
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            val shape = if (m.user)
                RoundedCornerShape(topStart = 18.dp, topEnd = 18.dp, bottomEnd = 4.dp, bottomStart = 18.dp)
            else
                RoundedCornerShape(topStart = 4.dp, topEnd = 18.dp, bottomEnd = 18.dp, bottomStart = 18.dp)
            Box(
                Modifier
                    .widthIn(max = maxWidth)
                    .clip(shape)
                    .background(if (m.user) Kat.accent else Kat.surface)
                    .then(if (m.user) Modifier else Modifier.border(1.dp, Kat.hairlineStrong, shape))
                    .padding(horizontal = 16.dp, vertical = 11.dp)
            ) {
                Text(
                    m.text.ifEmpty { "…" },
                    fontSize = 15.sp, lineHeight = 22.5.sp, fontFamily = Plex,
                    color = if (m.user) Kat.onAccent else if (m.text.isEmpty()) Kat.textFaint else Kat.textStrong,
                )
            }
            // Der Prototyp setzt hier "Uhrzeit · Agent". Msg traegt keine
            // Uhrzeit (und darf keine bekommen, sonst bricht der Chat-Sync),
            // deshalb bleibt der Agentenname.
            if (!m.user) Row(
                Modifier.padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(agentLabel, fontSize = 11.sp, fontFamily = Plex,
                    color = Kat.textSubtle, maxLines = 1)
                if (m.text.isNotBlank()) Icon(
                    Icons.Outlined.VolumeUp, "Vorlesen",
                    Modifier.size(15.dp).tap { onSpeak(m.text) }, tint = Kat.textGhost,
                )
            }
        }
    }
}

// ── Schublade ───────────────────────────────────────────────────────────────

@Composable
fun KatDrawer(
    conversations: List<Conversation>,
    currentId: String,
    agentCount: Int,
    online: Boolean,
    onSelect: (String) -> Unit,
    onNew: () -> Unit,
    onDelete: (Conversation) -> Unit,
    onTasks: () -> Unit,
    onSettings: () -> Unit,
) {
    ModalDrawerSheet(
        modifier = Modifier.fillMaxWidth(0.82f),
        drawerShape = RoundedCornerShape(0.dp),
        drawerContainerColor = Kat.elevated,
    ) {
        Row(
            Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 20.dp, bottom = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                Modifier.size(44.dp).clip(RoundedCornerShape(12.dp))
                    .background(Brush.linearGradient(listOf(Color(0xFF2D5C96), Color(0xFF1E3D63)))),
                contentAlignment = Alignment.Center,
            ) {
                Text("K", fontSize = 19.sp, fontWeight = FontWeight.Bold, fontFamily = Plex, color = Color(0xFFEAF2FB))
            }
            Column(Modifier.weight(1f)) {
                Text("KatAgent", style = MaterialTheme.typography.titleLarge, color = Kat.text)
                Text(
                    "$agentCount agents · ${if (online) "connected" else "offline"}",
                    fontSize = 12.sp, fontFamily = Plex, color = Kat.textFaint,
                )
            }
        }
        Box(Modifier.padding(start = 16.dp, end = 16.dp, bottom = 8.dp)) {
            FilledPill(
                "New chat", onNew, Modifier.fillMaxWidth(), height = 46.dp,
                leading = { Icon(Icons.Filled.Add, null, Modifier.size(17.dp), tint = Kat.onAccent) },
            )
        }
        Kicker("History", Modifier.padding(start = 20.dp, end = 20.dp, top = 16.dp, bottom = 6.dp))
        LazyColumn(
            Modifier.weight(1f).padding(horizontal = 8.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            items(conversations, key = { it.id }) { c ->
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (c.id == currentId) Kat.rowSel else Color.Transparent)
                        .tap { onSelect(c.id) }
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            c.title, fontSize = 14.5.sp, fontFamily = Plex, fontWeight = FontWeight.Medium,
                            color = Kat.textStrong, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                        val a = agentOf(c)
                        Text(
                            a, Modifier.padding(top = 2.dp), fontSize = 12.sp, fontFamily = PlexMono,
                            color = Kat.agent(a), maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                    }
                    RoundIconButton({ onDelete(c) }, size = 32.dp) {
                        Icon(Icons.Outlined.DeleteOutline, "Löschen", Modifier.size(16.dp), tint = Kat.textSubtle)
                    }
                }
            }
        }
        Hairline()
        Column(Modifier.padding(8.dp), verticalArrangement = Arrangement.spacedBy(1.dp)) {
            DrawerAction("Tasks", Icons.Outlined.Checklist, onTasks)
            DrawerAction("Settings", Icons.Outlined.Settings, onSettings)
        }
    }
}

@Composable
private fun DrawerAction(
    label: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    onClick: () -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .tap { onClick() }
            .padding(horizontal = 12.dp, vertical = 11.dp),
        horizontalArrangement = Arrangement.spacedBy(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, Modifier.size(18.dp), tint = Kat.textDim)
        Text(label, fontSize = 14.5.sp, fontFamily = Plex, fontWeight = FontWeight.Medium, color = Kat.textDim)
    }
}

/** Kopf der beiden Vollbilder: Zurueck-Pfeil + Titel + Haarlinie. */
@Composable
private fun ScreenHeader(title: String, onClose: () -> Unit) {
    Column {
        Row(
            Modifier.fillMaxWidth().padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            RoundIconButton(onClose) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, "Zurück", Modifier.size(20.dp), tint = Kat.textDim)
            }
            Text(
                title, Modifier.weight(1f).padding(start = 4.dp, top = 8.dp, bottom = 8.dp),
                style = MaterialTheme.typography.titleMedium, color = Kat.text,
            )
        }
        Hairline()
    }
}

// ── Tasks ───────────────────────────────────────────────────────────────────

@Composable
fun TasksScreen(
    prefs: Prefs,
    instances: List<AgentInstance>,
    onClose: () -> Unit,
    onStatus: (String) -> Unit,
    onOpenChat: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var tasks by remember { mutableStateOf<List<AgentTask>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var reload by remember { mutableStateOf(0) }
    var expanded by remember { mutableStateOf("") }
    val targets = if (instances.isNotEmpty()) instances.map { it.name }
        else if (prefs.instance.isNotBlank()) listOf(prefs.instance) else emptyList()
    var target by remember { mutableStateOf(prefs.instance.ifBlank { targets.firstOrNull() ?: "" }) }
    var message by remember { mutableStateOf("") }
    var schedule by remember { mutableStateOf("") }

    LaunchedEffect(reload) {
        loading = true
        val j = withContext(Dispatchers.IO) { ManagerSync.listTasks(prefs.serverUrl, prefs.user, prefs.pass) }
        loading = false
        if (j == null) onStatus("⚠️ Aufgaben nicht ladbar: ${ManagerSync.lastStatus}") else tasks = ManagerSync.parseTasks(j)
    }
    LaunchedEffect(Unit) { while (true) { delay(5000); reload++ } }

    Column(Modifier.fillMaxSize().background(Kat.bg)) {
        ScreenHeader("Tasks", onClose)
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Der Prototyp zeigt nur die Liste. Ohne dieses Feld liesse sich in
            // der App aber keine Aufgabe mehr anlegen (nur noch per /task).
            KatCard {
                Kicker("Neue Aufgabe")
                if (targets.isEmpty()) {
                    Text("Kein Server-Agent verfügbar.", fontSize = 13.sp, fontFamily = Plex, color = Kat.textFaint)
                } else {
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        targets.forEach { n ->
                            AgentChip(n, target == n, { target = n }) {
                                Icon(Icons.Filled.Cloud, null, Modifier.size(14.dp), tint = it)
                            }
                        }
                    }
                    KatField(message, { message = it }, placeholder = "Auftrag")
                    KatField(schedule, { schedule = it }, placeholder = "Zeitplan (leer = einmalig)", mono = true)
                    FilledPill(
                        if (schedule.isBlank()) "Im Hintergrund starten" else "Zeitplan anlegen",
                        {
                            val m = message.trim()
                            if (m.isNotBlank() && target.isNotBlank()) {
                                onStatus("Aufgabe wird angelegt…")
                                scope.launch {
                                    val r = withContext(Dispatchers.IO) {
                                        ManagerSync.createTask(prefs.serverUrl, prefs.user, prefs.pass, target, m, schedule.trim())
                                    }
                                    message = ""
                                    onStatus(r?.let { "" } ?: "⚠️ ${ManagerSync.lastStatus}")
                                    reload++
                                }
                            }
                        },
                        Modifier.fillMaxWidth(),
                        enabled = message.isNotBlank() && target.isNotBlank(),
                    )
                }
            }

            if (tasks.isEmpty() && !loading) Text(
                "Noch keine Aufgaben.", Modifier.padding(top = 4.dp),
                fontSize = 13.sp, fontFamily = Plex, color = Kat.textFaint,
            )
            tasks.forEach { t ->
                val done = t.status == "done"
                Column(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(Kat.surface)
                        .border(1.dp, Kat.hairlineStrong, RoundedCornerShape(14.dp))
                        // Erster Tipp klappt auf, zweiter springt in den
                        // Aufgaben-Chat der Instanz — dort laesst sich das
                        // Ergebnis direkt weiterverhandeln.
                        .tap { if (expanded == t.id) onOpenChat(t.instance) else expanded = t.id }
                        .padding(horizontal = 16.dp, vertical = 14.dp),
                ) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        // Anzeige, kein Schalter: der Manager kennt kein
                        // "abhaken", nur den Status des Laufs.
                        Box(
                            Modifier.size(24.dp).clip(CircleShape)
                                .background(if (done) Kat.green else Color.Transparent)
                                .border(2.dp, if (done) Kat.green else Kat.textGhost, CircleShape),
                            contentAlignment = Alignment.Center,
                        ) {
                            if (done) Icon(Icons.Filled.Check, null, Modifier.size(12.dp), tint = Kat.bg)
                        }
                        Column(Modifier.weight(1f)) {
                            Text(
                                t.message, fontSize = 14.5.sp, fontFamily = Plex, fontWeight = FontWeight.Medium,
                                color = if (done) Kat.textSubtle else Kat.textStrong,
                                textDecoration = if (done) TextDecoration.LineThrough else TextDecoration.None,
                                maxLines = 2, overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                t.instance + " · " + t.schedule.ifBlank { "einmalig" },
                                Modifier.padding(top = 3.dp),
                                fontSize = 12.sp, fontFamily = PlexMono, color = Kat.agent(t.instance),
                                maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                        val (bg, fg, bd) = when {
                            done -> Triple(Color(0x1A4CC38A), Kat.green, Color(0x404CC38A))
                            t.status == "error" -> Triple(Color(0x1AE06C75), Kat.red, Color(0x40E06C75))
                            t.status == "running" || t.status == "pending" ->
                                Triple(Color(0x1A7FB0E8), Kat.accentText, Color(0x4D7FB0E8))
                            else -> Triple(Kat.hover, Kat.textMuted, Color(0x1AFFFFFF))
                        }
                        StatusBadge(t.status, bg, fg, bd)
                        RoundIconButton({
                            scope.launch {
                                withContext(Dispatchers.IO) { ManagerSync.deleteTask(prefs.serverUrl, prefs.user, prefs.pass, t.id) }
                                reload++
                            }
                        }, size = 32.dp) {
                            Icon(Icons.Outlined.DeleteOutline, "Löschen", Modifier.size(16.dp), tint = Kat.textSubtle)
                        }
                    }
                    if (expanded == t.id) {
                        // Oben ist der Auftrag auf zwei Zeilen gekuerzt —
                        // aufgeklappt gehoert er vollstaendig hin.
                        if (t.message.length > 90) Text(
                            t.message, Modifier.padding(top = 10.dp),
                            fontSize = 13.sp, fontFamily = Plex, color = Kat.textSubtle,
                        )
                        if (t.result.isNotBlank()) Text(
                            t.result, Modifier.padding(top = 10.dp),
                            fontSize = 13.sp, fontFamily = Plex, color = Kat.textFaint,
                        )
                        Row(
                            Modifier.padding(top = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Icon(Icons.Outlined.ChatBubbleOutline, null,
                                Modifier.size(14.dp), tint = Kat.accentText)
                            Text(
                                "Nochmal tippen: im Chat mit @${t.instance} weiterreden",
                                fontSize = 12.sp, fontFamily = Plex, color = Kat.accentText,
                            )
                        }
                    }
                }
            }
        }
    }
}

// ── Einstellungen ───────────────────────────────────────────────────────────

@Composable
fun SettingsScreen(
    prefs: Prefs,
    store: ChatStore,
    dl: Dl,
    web: Boolean,
    onWeb: (Boolean) -> Unit,
    syncing: Boolean,
    lastSync: String,
    online: Boolean,
    onClose: () -> Unit,
    onSelectModel: (File) -> Unit,
    onDeleteModel: (File) -> Unit,
    onPickModel: () -> Unit,
    onDownload: (String, String) -> Unit,
    onManageAgents: () -> Unit,
    onSync: () -> Unit,
) {
    val ctx = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val ver = remember {
        try { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "" } catch (e: Exception) { "" }
    }
    var url by remember { mutableStateOf(prefs.serverUrl) }
    var instance by remember { mutableStateOf(prefs.instance) }
    var user by remember { mutableStateOf(prefs.user) }
    var pass by remember { mutableStateOf(prefs.pass) }
    var token by remember { mutableStateOf(prefs.hfToken) }
    var modelUrl by remember { mutableStateOf(prefs.modelUrl) }
    var models by remember { mutableStateOf(store.models()) }
    var copied by remember { mutableStateOf(false) }
    // Der Prototyp hat keinen Speichern-Knopf: die Felder schreiben direkt.
    LaunchedEffect(copied) { if (copied) { delay(2000); copied = false } }
    // Nach einem fertigen Download taucht das Modell in der Liste auf.
    LaunchedEffect(dl) { models = store.models() }

    Column(Modifier.fillMaxSize().background(Kat.bg)) {
        ScreenHeader("Settings", onClose)
        Column(
            Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            // ── Modelle (On-Device) ────────────────────────────────────────
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Kicker("Modelle (On-Device)", Modifier.padding(horizontal = 4.dp))
                KatCard(padding = PaddingValues(4.dp), spacing = 0.dp) {
                    if (models.isEmpty()) Text(
                        "Noch kein Modell geladen.", Modifier.padding(horizontal = 12.dp, vertical = 14.dp),
                        fontSize = 13.sp, fontFamily = Plex, color = Kat.textFaint,
                    )
                    models.forEach { f ->
                        val sel = prefs.activeModel == f.name
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                .tap { onSelectModel(f) }
                                .padding(horizontal = 12.dp, vertical = 11.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(
                                Modifier.size(20.dp).clip(CircleShape)
                                    .border(2.dp, if (sel) Kat.accentText else Kat.textGhost, CircleShape),
                                contentAlignment = Alignment.Center,
                            ) {
                                if (sel) Box(Modifier.size(10.dp).clip(CircleShape).background(Kat.accentText))
                            }
                            Column(Modifier.weight(1f)) {
                                Text(f.name, fontSize = 13.sp, fontFamily = PlexMono, color = Kat.textStrong)
                                Text(
                                    "${f.length() / 1_000_000} MB", Modifier.padding(top = 2.dp),
                                    fontSize = 12.sp, fontFamily = Plex, color = Kat.textFaint,
                                )
                            }
                            RoundIconButton({ onDeleteModel(f); models = store.models() }, size = 36.dp) {
                                Icon(Icons.Outlined.DeleteOutline, "Löschen", Modifier.size(17.dp), tint = Kat.textSubtle)
                            }
                        }
                    }
                    (dl as? Dl.Progress)?.let { p ->
                        val pct = if (p.total > 0) (p.done * 100 / p.total).toInt() else 0
                        Column(
                            Modifier.padding(horizontal = 12.dp, vertical = 11.dp),
                            verticalArrangement = Arrangement.spacedBy(7.dp),
                        ) {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text(
                                    modelUrl.substringAfterLast('/'), Modifier.weight(1f),
                                    fontSize = 12.sp, fontFamily = PlexMono, color = Kat.textFaint,
                                    maxLines = 1, overflow = TextOverflow.Ellipsis,
                                )
                                Text("$pct%", fontSize = 12.sp, fontFamily = PlexMono, color = Kat.textFaint)
                            }
                            Box(Modifier.fillMaxWidth().height(5.dp).clip(RoundedCornerShape(3.dp)).background(Kat.bg)) {
                                Box(Modifier.fillMaxWidth(pct / 100f).height(5.dp)
                                    .clip(RoundedCornerShape(3.dp)).background(Kat.accent))
                            }
                        }
                    }
                }
            }

            // ── Server-Verbindung ──────────────────────────────────────────
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Kicker("Server-Verbindung", Modifier.padding(horizontal = 4.dp))
                KatCard {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        LabeledField("Server-URL", url, { url = it; prefs.serverUrl = it.trim() },
                            Modifier.weight(1f), mono = true)
                        StatusBadge(
                            if (online) "connected" else "offline",
                            if (online) Color(0x1A4CC38A) else Kat.hover,
                            if (online) Kat.green else Kat.textMuted,
                            if (online) Color(0x404CC38A) else Color(0x1AFFFFFF),
                            Modifier.padding(top = 20.dp),
                        )
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        LabeledField("Benutzer", user, { user = it; prefs.user = it.trim() }, Modifier.weight(1f))
                        LabeledField("Passwort", pass, { pass = it; prefs.pass = it }, Modifier.weight(1f), password = true)
                    }
                    LabeledField("Aktive Instanz", instance, { instance = it; prefs.instance = it.trim() }, mono = true)
                    FilledPill(
                        "Server-Agenten verwalten", onManageAgents, Modifier.fillMaxWidth(),
                        trailing = {
                            Icon(Icons.AutoMirrored.Filled.ArrowForward, null, Modifier.size(15.dp), tint = Kat.onAccent)
                        },
                    )
                    Text(
                        "Alle Agenten auflisten, aktiven wählen, anlegen, starten/stoppen/löschen.",
                        fontSize = 11.5.sp, lineHeight = 17.sp, fontFamily = Plex, color = Kat.textSubtle,
                    )
                    Hairline(color = Kat.hairline)
                    Row(
                        Modifier.fillMaxWidth().tap { onSync() },
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("Auto-sync", fontSize = 14.5.sp, fontFamily = Plex,
                                fontWeight = FontWeight.Medium, color = Kat.text)
                            Text(
                                "Last sync " + lastSync.ifBlank { "—" }, Modifier.padding(top = 2.dp),
                                fontSize = 12.5.sp, fontFamily = Plex, color = Kat.textFaint,
                            )
                        }
                        Text(
                            if (syncing) "running …" else "up to date",
                            fontSize = 12.5.sp, fontFamily = Plex, color = Kat.textFaint,
                        )
                    }
                }
            }

            // ── Chat ───────────────────────────────────────────────────────
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Kicker("Chat", Modifier.padding(horizontal = 4.dp))
                KatCard(padding = PaddingValues(4.dp), spacing = 0.dp) {
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                            .tap { onWeb(!web) }.padding(12.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("Web-Zugriff", fontSize = 14.5.sp, fontFamily = Plex,
                                fontWeight = FontWeight.Medium, color = Kat.text)
                            Text(
                                "Suchergebnisse als Kontext für das Gerätemodell", Modifier.padding(top = 2.dp),
                                fontSize = 12.5.sp, fontFamily = Plex, color = Kat.textFaint,
                            )
                        }
                        KatSwitch(web, { onWeb(!web) })
                    }
                }
            }

            // ── Modell herunterladen ───────────────────────────────────────
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Kicker("Modell herunterladen", Modifier.padding(horizontal = 4.dp))
                KatCard {
                    LabeledField("HuggingFace-Token", token, { token = it; prefs.hfToken = it.trim() },
                        mono = true, password = true)
                    LabeledField("Modell-URL (.litertlm)", modelUrl, { modelUrl = it; prefs.modelUrl = it.trim() },
                        mono = true, fontSize = 12f)
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text(
                                "Modell-Katalog (litert-community)", Modifier.weight(1f),
                                fontSize = 12.sp, fontFamily = Plex, color = Kat.textSubtle,
                            )
                            Text(
                                if (copied) "Link kopiert ✓" else "Link zum Katalog kopieren",
                                Modifier.tap {
                                    clipboard.setText(AnnotatedString("https://huggingface.co/models?library=litert-lm"))
                                    copied = true
                                },
                                fontSize = 11.5.sp, fontFamily = Plex, color = Kat.accentText,
                            )
                        }
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            PRESETS.forEach { p ->
                                val sel = modelUrl == p.url
                                Row(
                                    Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                                        .background(if (sel) Kat.chipSel else Color.Transparent)
                                        .border(1.dp, if (sel) Kat.borderFocus else Kat.border, RoundedCornerShape(10.dp))
                                        .tap { modelUrl = p.url; prefs.modelUrl = p.url }
                                        .padding(horizontal = 11.dp, vertical = 9.dp),
                                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            p.name, fontSize = 12.5.sp, fontFamily = PlexMono,
                                            color = if (sel) Kat.accentBright else Kat.textDim,
                                        )
                                        Text(
                                            p.desc, Modifier.padding(top = 1.dp),
                                            fontSize = 11.5.sp, fontFamily = Plex, color = Kat.textSubtle,
                                        )
                                    }
                                    val (tbg, tfg) = when (p.tag) {
                                        "multimodal" -> Color(0x1F7FB0E8) to Kat.accentText
                                        "klein" -> Color(0x1F4CC38A) to Kat.green
                                        else -> Kat.hairline to Kat.textMuted
                                    }
                                    MiniTag(p.tag, tbg, tfg)
                                    Text(p.size, fontSize = 11.5.sp, fontFamily = PlexMono, color = Kat.textFaint)
                                }
                            }
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        FilledPill(
                            "Herunterladen",
                            { onDownload(modelUrl.trim(), token.trim()) },
                            Modifier.weight(1f),
                            enabled = modelUrl.isNotBlank(),
                            leading = { Icon(Icons.Outlined.Download, null, Modifier.size(15.dp), tint = Kat.onAccent) },
                        )
                        OutlinePill("Datei wählen", onPickModel, Modifier.weight(1f))
                    }
                }
            }

            Text(
                "KatAgent ${ver.ifBlank { "" }} · de.kat56.agent",
                Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 4.dp),
                fontSize = 12.sp, fontFamily = Plex, color = Kat.textGhost,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            )
        }
    }
}

// ── Server-Agenten (im Prototyp nicht gezeichnet — unveraendert uebernommen) ─

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ServerAgentsDialog(prefs: Prefs, onDismiss: () -> Unit, onStatus: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    var instances by remember { mutableStateOf<List<AgentInstance>>(emptyList()) }
    var active by remember { mutableStateOf(prefs.instance) }
    var loading by remember { mutableStateOf(false) }
    var busyName by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    var template by remember { mutableStateOf("openrouter") }
    var model by remember { mutableStateOf("google/gemma-4-26b-a4b-it:free") }
    var personas by remember { mutableStateOf<List<Persona>>(emptyList()) }
    var persona by remember { mutableStateOf("") }

    fun refresh() {
        scope.launch {
            loading = true
            val j = withContext(Dispatchers.IO) { ManagerSync.listInstances(prefs.serverUrl, prefs.user, prefs.pass) }
            loading = false
            if (j == null) { onStatus("⚠️ Server nicht erreichbar: ${ManagerSync.lastStatus}"); return@launch }
            instances = ManagerSync.parseInstances(j)
        }
    }
    LaunchedEffect(Unit) {
        refresh()
        val pj = withContext(Dispatchers.IO) { ManagerSync.listPersonas(prefs.serverUrl, prefs.user, prefs.pass) }
        personas = ManagerSync.parsePersonas(pj)
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Kat.surface,
        confirmButton = { TextButton(onDismiss) { Text("Schließen") } },
        dismissButton = { TextButton({ refresh() }, enabled = !loading) { Text("Aktualisieren") } },
        title = { Text("Server-Agenten") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                if (!loading && instances.isEmpty()) Text("Keine Instanzen gefunden.", style = MaterialTheme.typography.bodySmall)
                instances.forEach { inst ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(active == inst.name, {
                            active = inst.name; prefs.instance = inst.name; onStatus("Aktiv: ${inst.name}")
                        })
                        Column(Modifier.weight(1f).padding(end = 4.dp)) {
                            Text(inst.name, style = MaterialTheme.typography.bodyMedium, maxLines = 1,
                                overflow = TextOverflow.Ellipsis)
                            val sub = buildString {
                                append(if (inst.running) "● läuft" else "○ aus")
                                if (inst.template.isNotEmpty()) append(" · ${inst.template}")
                            }
                            Text(sub, style = MaterialTheme.typography.labelMedium,
                                color = if (inst.running) Kat.green else Kat.textFaint)
                            if (inst.model.isNotEmpty()) Text(inst.model,
                                fontSize = 12.sp, fontFamily = PlexMono, color = Kat.textFaint,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        if (busyName == inst.name) {
                            CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                        } else {
                            if (inst.running) {
                                IconButton({
                                    val base = prefs.serverUrl.trimEnd('/')
                                    ctx.startActivity(
                                        Intent(ctx, TerminalActivity::class.java)
                                            .putExtra("url", "$base/i/${inst.name}/term/")
                                            .putExtra("title", "Terminal · ${inst.name}")
                                    )
                                }) { Icon(Icons.Filled.Terminal, "Terminal", Modifier.size(20.dp)) }
                            }
                            TextButton({
                                val act = if (inst.running) "stop" else "start"
                                busyName = inst.name
                                scope.launch {
                                    withContext(Dispatchers.IO) { ManagerSync.action(prefs.serverUrl, prefs.user, prefs.pass, inst.name, act) }
                                    busyName = ""; refresh()
                                }
                            }, contentPadding = PaddingValues(horizontal = 10.dp)) {
                                Text(if (inst.running) "Stop" else "Start", maxLines = 1)
                            }
                            IconButton({
                                busyName = inst.name
                                scope.launch {
                                    withContext(Dispatchers.IO) { ManagerSync.action(prefs.serverUrl, prefs.user, prefs.pass, inst.name, "delete") }
                                    busyName = ""
                                    if (active == inst.name) { active = ""; prefs.instance = "" }
                                    refresh()
                                }
                            }) { Icon(Icons.Outlined.DeleteOutline, "Löschen", Modifier.size(20.dp)) }
                        }
                    }
                }
                HorizontalDivider(Modifier.padding(vertical = 4.dp), color = Kat.hairline)
                Text("Neuen Agenten anlegen", style = MaterialTheme.typography.labelLarge)
                OutlinedTextField(name, { name = it }, label = { Text("Name (z. B. gemma4)") },
                    singleLine = true, modifier = Modifier.fillMaxWidth())
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("openrouter", "prime", "pi", "claude").forEach { tpl ->
                        FilterChip(template == tpl, { template = tpl }, { Text(tpl, maxLines = 1) })
                    }
                }
                if (template != "claude") {
                    OutlinedTextField(model, { model = it }, label = { Text("Modell") },
                        singleLine = true, modifier = Modifier.fillMaxWidth())
                }
                if (personas.isNotEmpty()) {
                    Text("Persona (System-Prompt)", style = MaterialTheme.typography.labelMedium)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        FilterChip(persona == "", { persona = "" }, { Text("Standard", maxLines = 1) })
                        personas.forEach { p ->
                            FilterChip(persona == p.name, { persona = p.name }, { Text(p.name, maxLines = 1) })
                        }
                    }
                }
                Button(
                    onClick = {
                        val n = name.trim()
                        if (n.isBlank()) return@Button
                        busyName = n; onStatus("Agent '$n' wird angelegt…")
                        scope.launch {
                            val cfg = JSONObject().put("TRANSPORT", "web")
                            when (template) {
                                "openrouter" -> cfg.put("OPENROUTER_MODEL", model.trim())
                                "pi" -> cfg.put("PI_MODEL", model.trim())
                            }
                            personas.firstOrNull { it.name == persona }?.let { cfg.put("AGENT_SYSTEM", it.prompt) }
                            val res = withContext(Dispatchers.IO) {
                                ManagerSync.createAndStart(prefs.serverUrl, prefs.user, prefs.pass, n, template, cfg)
                            }
                            prefs.instance = n; active = n; name = ""; onStatus(res); busyName = ""; refresh()
                        }
                    },
                    enabled = name.isNotBlank() && busyName.isEmpty(),
                ) { Text("Anlegen & starten") }
            }
        }
    )
}

private fun copyModel(context: Context, dir: File, uri: Uri): String? {
    return try {
        val name = "modell-gewaehlt.litertlm"
        val out = File(dir.apply { mkdirs() }, name)
        context.contentResolver.openInputStream(uri)?.use { inp -> out.outputStream().use { o -> inp.copyTo(o, 1 shl 20) } }
        out.absolutePath
    } catch (e: Exception) { null }
}

private fun bitmapToBase64(bmp: Bitmap): String {
    val out = java.io.ByteArrayOutputStream()
    bmp.compress(Bitmap.CompressFormat.JPEG, 85, out)
    return android.util.Base64.encodeToString(out.toByteArray(), android.util.Base64.NO_WRAP)
}

private fun loadBitmap(context: Context, uri: Uri): Bitmap? {
    return try {
        val raw = context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) } ?: return null
        val max = 768
        val w = raw.width; val h = raw.height
        val scaled = if (w <= max && h <= max) raw else {
            val s = max.toFloat() / maxOf(w, h)
            Bitmap.createScaledBitmap(raw, (w * s).toInt().coerceAtLeast(1), (h * s).toInt().coerceAtLeast(1), true)
        }
        if (scaled.config == Bitmap.Config.ARGB_8888) scaled else scaled.copy(Bitmap.Config.ARGB_8888, false)
    } catch (e: Exception) { null }
}
