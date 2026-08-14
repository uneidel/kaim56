package de.kat56.agent

import android.Manifest
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
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.outlined.AddPhotoAlternate
import androidx.compose.material.icons.outlined.Checklist
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        val gemma = LocalGemma(this)
        val store = ChatStore(this)
        store.migrate(prefs)   // v1.0-Modell uebernehmen, falls vorhanden
        setContent {
            val dark = isSystemInDarkTheme()
            // Feste Industry-Palette (wie der Manager) statt Material-You-Dynamic-Color.
            val colors = if (dark) IndustryDark else IndustryLight
            MaterialTheme(
                colorScheme = colors,
                typography = IndustryTypography,
                shapes = IndustryShapes,
            ) {
                KatAgentApp(prefs, gemma, store)
            }
        }
    }
}

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
    var showSettings by remember { mutableStateOf(false) }
    var showAgents by remember { mutableStateOf(false) }
    var showTasks by remember { mutableStateOf(false) }
    var pendingImage by remember { mutableStateOf<Bitmap?>(null) }
    var web by remember { mutableStateOf(prefs.webAccess) }
    var instances by remember { mutableStateOf<List<AgentInstance>>(emptyList()) }
    val listState = rememberLazyListState()

    fun loadInstances() {
        if (prefs.serverUrl.isBlank()) return
        scope.launch {
            val j = withContext(Dispatchers.IO) { ManagerSync.listInstances(prefs.serverUrl, prefs.user, prefs.pass) }
            if (j != null) instances = ManagerSync.parseInstances(j)
        }
    }
    // Beim Start laden und nach dem Schließen der Agenten-Verwaltung erneut.
    LaunchedEffect(showAgents) { if (!showAgents) loadInstances() }

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

    fun sync() {
        if (prefs.serverUrl.isBlank()) { status = "⚠️ Server-URL fehlt (Einstellungen)"; return }
        scope.launch {
            status = "Sync…"
            val remoteJson = withContext(Dispatchers.IO) { ManagerSync.pull(prefs.serverUrl, prefs.user, prefs.pass) }
            if (remoteJson == null) { status = "⚠️ Sync: Server nicht erreichbar"; return@launch }
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
            status = if (ok) "Sync ✅ (${conversations.size} Chats)" else "⚠️ Push fehlgeschlagen"
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
            if (res == null) { delay(5000); continue }   // offline / alter Manager
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
                        "✅ Aufgabe auf @$inst angelegt${if (schedule.isNotBlank()) " ($schedule)" else " (Hintergrund)"}. Menü → Aufgaben."
                        else "⚠️ ${ManagerSync.lastStatus}"))
                    persist()
                }
            }
            "agents" -> { msgs.add(Msg(false, "Öffne Server-Agenten…")); showAgents = true }
            else -> msgs.add(Msg(false, "Unbekannter Befehl /$cmd — /help für Hilfe."))
        }
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
                }
            }
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            DrawerContent(
                conversations.sortedByDescending { it.updatedAt },
                currentId,
                onSelect = { currentId = it; scope.launch { drawerState.close() } },
                onNew = { newChat(); scope.launch { drawerState.close() } },
                onDelete = { deleteChat(it) },
                onSync = { sync(); scope.launch { drawerState.close() } },
                onTasks = { showTasks = true; scope.launch { drawerState.close() } },
                onSettings = { showSettings = true; scope.launch { drawerState.close() } },
            )
        }
    ) {
        Scaffold(
            topBar = {
                CenterAlignedTopAppBar(
                    title = { Text(current.title, maxLines = 1) },
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Filled.Menu, "Chats")
                        }
                    }
                )
            }
        ) { pad ->
            Column(Modifier.padding(pad).fillMaxSize()) {
                val chips = if (instances.isNotEmpty()) instances
                    else if (prefs.instance.isNotBlank()) listOf(AgentInstance(prefs.instance, false, "", "", "")) else emptyList()
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                        .padding(horizontal = 12.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    FilterChip(
                        selected = current.mode == "local",
                        onClick = { switchToAgent("local", "") },
                        leadingIcon = { Icon(Icons.Filled.PhoneAndroid, null, Modifier.size(18.dp)) },
                        label = { Text("Gerät") }
                    )
                    chips.forEach { inst ->
                        FilterChip(
                            selected = current.mode == "server" && current.instance == inst.name,
                            onClick = { switchToAgent("server", inst.name) },
                            leadingIcon = {
                                Icon(
                                    if (inst.running) Icons.Filled.Cloud else Icons.Outlined.CloudOff,
                                    null, Modifier.size(18.dp)
                                )
                            },
                            label = { Text(inst.name) }
                        )
                    }
                    IconButton({ loadInstances() }) {
                        Icon(Icons.Filled.Sync, "Agenten aktualisieren", Modifier.size(20.dp))
                    }
                }
                if (status.isNotEmpty()) Text(
                    status,
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 2.dp),
                    style = MaterialTheme.typography.labelSmall,
                    color = if (status.startsWith("⚠️")) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                    maxLines = 2
                )
                // Modell der aktuell gewaehlten Server-Instanz zeigen (wie im Manager).
                if (current.mode == "server") {
                    val m = chips.firstOrNull { it.name == current.instance }?.model ?: ""
                    if (m.isNotEmpty()) Text(
                        "🧠 $m",
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 2.dp),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1
                    )
                }
                if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    if (current.messages.isEmpty()) {
                        val sel = instances.firstOrNull { it.name == current.instance }
                        EmptyState(current.mode, current.instance,
                            sel?.running ?: false, sel?.model ?: "")
                    } else {
                        LazyColumn(
                            state = listState,
                            modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                            contentPadding = PaddingValues(vertical = 10.dp)
                        ) { items(current.messages) { m -> Bubble(m) } }
                    }
                }
                if (pendingImage != null) Row(
                    Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Image(
                        pendingImage!!.asImageBitmap(), "Anhang",
                        Modifier.size(40.dp).clip(RectangleShape),
                        contentScale = ContentScale.Crop
                    )
                    Spacer(Modifier.width(10.dp))
                    Text("Bild angehängt", style = MaterialTheme.typography.labelMedium)
                    Spacer(Modifier.weight(1f))
                    IconButton({ pendingImage = null }) { Icon(Icons.Filled.Close, "Entfernen") }
                }
                Surface(tonalElevation = 3.dp) {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.Bottom,
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        if (current.mode == "local") FilledTonalIconButton(
                            { web = !web; prefs.webAccess = web }, enabled = !busy,
                            colors = if (web) IconButtonDefaults.filledTonalIconButtonColors(
                                containerColor = MaterialTheme.colorScheme.primary,
                                contentColor = MaterialTheme.colorScheme.onPrimary
                            ) else IconButtonDefaults.filledTonalIconButtonColors()
                        ) { Icon(Icons.Outlined.Public, "Web-Zugriff (an/aus)") }
                        FilledTonalIconButton(
                            { imagePicker.launch("image/*") }, enabled = !busy
                        ) { Icon(Icons.Outlined.AddPhotoAlternate, "Bild anhängen") }
                        OutlinedTextField(
                            input, { input = it }, Modifier.weight(1f),
                            placeholder = { Text(if (current.mode == "server") "Nachricht…" else "Frag Gemma…") },
                            maxLines = 5,
                            shape = RectangleShape
                        )
                        FilledIconButton(
                            { send() },
                            enabled = !busy && (input.isNotBlank() || pendingImage != null)
                        ) { Icon(Icons.AutoMirrored.Filled.Send, "Senden") }
                    }
                }
            }
        }
    }

    if (showSettings) {
        SettingsDialog(
            prefs, store,
            onDismiss = { showSettings = false },
            onSelectModel = { f ->
                prefs.activeModel = f.name; showSettings = false; status = "Modell wird geladen…"
                scope.launch {
                    withContext(Dispatchers.IO) { try { gemma.load(f.absolutePath) } catch (e: Exception) { status = "⚠️ ${e.message}" } }
                    if (gemma.isReady()) status = "Modell geladen ✅"
                }
            },
            onDeleteModel = { f -> f.delete(); if (prefs.activeModel == f.name) { prefs.activeModel = ""; gemma.close() } },
            onPickModel = { picker.launch(arrayOf("application/octet-stream", "*/*")) },
            onDownload = { url, token -> showSettings = false; DownloadService.start(context, url, token); status = "Download startet… (Hintergrund)" },
            onManageAgents = { showSettings = false; showAgents = true },
        )
    }

    if (showAgents) {
        ServerAgentsDialog(prefs, onDismiss = { showAgents = false }, onStatus = { status = it })
    }

    if (showTasks) {
        TasksDialog(prefs, instances, onDismiss = { showTasks = false }, onStatus = { status = it })
    }
}

@Composable
fun DrawerContent(
    conversations: List<Conversation>,
    currentId: String,
    onSelect: (String) -> Unit,
    onNew: () -> Unit,
    onDelete: (Conversation) -> Unit,
    onSync: () -> Unit,
    onTasks: () -> Unit,
    onSettings: () -> Unit,
) {
    ModalDrawerSheet {
        Row(
            Modifier.fillMaxWidth().padding(20.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Surface(shape = RectangleShape, color = MaterialTheme.colorScheme.primary, modifier = Modifier.size(36.dp)) {
                Box(contentAlignment = Alignment.Center) {
                    Text("56", style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.onPrimary)
                }
            }
            Text("KatAgent", style = MaterialTheme.typography.titleLarge)
        }
        NavigationDrawerItem(
            label = { Text("Neuer Chat") },
            selected = false,
            icon = { Icon(Icons.Outlined.ChatBubbleOutline, null) },
            onClick = onNew,
            modifier = Modifier.padding(horizontal = 12.dp)
        )
        HorizontalDivider(Modifier.padding(vertical = 6.dp))
        LazyColumn(Modifier.weight(1f)) {
            items(conversations) { c ->
                NavigationDrawerItem(
                    label = {
                        Column {
                            Text(c.title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(4.dp)
                            ) {
                                val server = c.mode == "server"
                                Icon(
                                    if (server) Icons.Filled.Cloud else Icons.Filled.PhoneAndroid,
                                    null, Modifier.size(13.dp)
                                )
                                Text(
                                    if (server) c.instance.ifBlank { "Server" } else "Gerät",
                                    style = MaterialTheme.typography.labelSmall,
                                    maxLines = 1, overflow = TextOverflow.Ellipsis
                                )
                            }
                        }
                    },
                    selected = c.id == currentId,
                    onClick = { onSelect(c.id) },
                    badge = { IconButton({ onDelete(c) }) { Icon(Icons.Outlined.DeleteOutline, "Löschen") } },
                    modifier = Modifier.padding(horizontal = 12.dp)
                )
            }
        }
        HorizontalDivider()
        NavigationDrawerItem(
            label = { Text("Aufgaben") },
            selected = false,
            icon = { Icon(Icons.Outlined.Checklist, null) },
            onClick = onTasks,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 2.dp)
        )
        NavigationDrawerItem(
            label = { Text("Mit Server synchronisieren") },
            selected = false,
            icon = { Icon(Icons.Filled.Sync, null) },
            onClick = onSync,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 2.dp)
        )
        NavigationDrawerItem(
            label = { Text("Einstellungen") },
            selected = false,
            icon = { Icon(Icons.Outlined.Settings, null) },
            onClick = onSettings,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 2.dp)
        )
    }
}

@Composable
fun EmptyState(mode: String, instance: String = "", running: Boolean = false, model: String = "") {
    val title = if (mode == "server") (instance.ifBlank { "Server-Agent" }) else "Gerät – Gemma offline"
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Surface(shape = RectangleShape, color = MaterialTheme.colorScheme.primary, modifier = Modifier.size(76.dp)) {
            Box(contentAlignment = Alignment.Center) {
                if (mode == "server")
                    Icon(Icons.Filled.Cloud, null, Modifier.size(34.dp), tint = MaterialTheme.colorScheme.onPrimary)
                else
                    Icon(Icons.Filled.PhoneAndroid, null, Modifier.size(34.dp), tint = MaterialTheme.colorScheme.onPrimary)
            }
        }
        Spacer(Modifier.height(18.dp))
        Text(title, style = MaterialTheme.typography.titleMedium)
        if (mode == "server" && instance.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                if (running) "● läuft" else "○ gestoppt – startet beim ersten Prompt",
                style = MaterialTheme.typography.labelMedium,
                color = if (running) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
            )
            if (model.isNotBlank()) Text(model, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "Stell eine Frage, um den Chat zu starten.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

@Composable
fun Bubble(m: Msg) {
    // Industry kennt keine getoenten Flaechen: die eigene Nachricht ist die
    // gefuellte Aktion (.btn-primary), die des Agenten eine Karte — transparent
    // mit Haarlinie. Eckig beides, wie alles im System.
    val fg = if (m.user) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onBackground
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (m.user) Arrangement.End else Arrangement.Start) {
        Box(
            Modifier
                .widthIn(max = 320.dp)
                .then(
                    if (m.user) Modifier.background(MaterialTheme.colorScheme.primary, RectangleShape)
                    else Modifier.border(1.dp, Industry.divider, RectangleShape)
                )
        ) {
            Text(
                m.text.ifEmpty { "…" },
                Modifier.padding(horizontal = IndustrySpacing.s4, vertical = IndustrySpacing.s3),
                color = fg,
                style = MaterialTheme.typography.bodyMedium
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsDialog(
    prefs: Prefs,
    store: ChatStore,
    onDismiss: () -> Unit,
    onSelectModel: (File) -> Unit,
    onDeleteModel: (File) -> Unit,
    onPickModel: () -> Unit,
    onDownload: (String, String) -> Unit,
    onManageAgents: () -> Unit,
) {
    val ctx = LocalContext.current
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
    val presets = listOf(
        "Gemma-4 E4B (~3,7 GB · multimodal)" to
            "https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm",
        "Gemma-4 E2B (~2,6 GB · multimodal)" to
            "https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm",
        "Gemma-3n E2B (~3,7 GB)" to
            "https://huggingface.co/google/gemma-3n-E2B-it-litert-lm/resolve/main/gemma-3n-E2B-it-int4.litertlm",
    )
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = {
            TextButton({
                prefs.serverUrl = url.trim(); prefs.instance = instance.trim(); prefs.user = user.trim()
                prefs.pass = pass; prefs.hfToken = token.trim(); prefs.modelUrl = modelUrl.trim(); onDismiss()
            }) { Text("Speichern") }
        },
        dismissButton = { TextButton(onDismiss) { Text("Schließen") } },
        title = { Text(if (ver.isEmpty()) "Einstellungen" else "Einstellungen · v$ver") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Modelle (On-Device)", style = MaterialTheme.typography.labelLarge)
                if (models.isEmpty()) Text("Noch kein Modell geladen.", style = MaterialTheme.typography.bodySmall)
                models.forEach { f ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(prefs.activeModel == f.name, { onSelectModel(f) })
                        Text("${f.name}  (${f.length() / 1_000_000} MB)", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                        IconButton({ onDeleteModel(f); models = store.models() }) { Icon(Icons.Outlined.DeleteOutline, "Löschen") }
                    }
                }
                OutlinedTextField(token, { token = it }, label = { Text("HuggingFace-Token") }, singleLine = true)
                OutlinedTextField(modelUrl, { modelUrl = it }, label = { Text("Modell-URL (.litertlm)") }, maxLines = 3)
                Text("Presets:", style = MaterialTheme.typography.labelSmall)
                presets.forEach { (name, u) ->
                    TextButton({ modelUrl = u }, contentPadding = PaddingValues(horizontal = 4.dp, vertical = 0.dp)) {
                        Text(name, style = MaterialTheme.typography.bodySmall)
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button({ onDownload(modelUrl.trim(), token.trim()) }, enabled = modelUrl.isNotBlank()) { Text("Herunterladen") }
                    OutlinedButton(onPickModel) { Text("Datei wählen") }
                }
                HorizontalDivider(Modifier.padding(vertical = 4.dp))
                Text("Server-Verbindung", style = MaterialTheme.typography.labelLarge)
                OutlinedTextField(url, { url = it }, label = { Text("Server-URL") }, singleLine = true)
                OutlinedTextField(user, { user = it }, label = { Text("Benutzer") }, singleLine = true)
                OutlinedTextField(pass, { pass = it }, label = { Text("Passwort") }, singleLine = true)
                OutlinedTextField(instance, { instance = it }, label = { Text("Aktive Instanz") }, singleLine = true)
                Button(onClick = {
                    prefs.serverUrl = url.trim(); prefs.user = user.trim(); prefs.pass = pass
                    onManageAgents()
                }) { Text("Server-Agenten verwalten →") }
                Text("Alle Agenten auflisten, aktiven wählen, anlegen, starten/stoppen/löschen.",
                    style = MaterialTheme.typography.labelSmall)
            }
        }
    )
}

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
                            Text(sub, style = MaterialTheme.typography.labelSmall,
                                color = if (inst.running) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                            if (inst.model.isNotEmpty()) Text(inst.model,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
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
                HorizontalDivider(Modifier.padding(vertical = 4.dp))
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksDialog(prefs: Prefs, instances: List<AgentInstance>, onDismiss: () -> Unit, onStatus: (String) -> Unit) {
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

    fun statusLabel(s: String) = when (s) {
        "pending" -> "⏳ wartet"; "running" -> "⏳ läuft"; "done" -> "✅ fertig"
        "error" -> "⚠️ Fehler"; "scheduled" -> "🕒 geplant"; else -> s
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onDismiss) { Text("Schließen") } },
        dismissButton = { TextButton({ reload++ }, enabled = !loading) { Text("Aktualisieren") } },
        title = { Text("Aufgaben") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                // --- Neue Aufgabe ---
                Text("Neue Aufgabe", style = MaterialTheme.typography.labelLarge)
                if (targets.isEmpty()) {
                    Text("Kein Server-Agent verfügbar (oben Instanz wählen/anlegen).",
                        style = MaterialTheme.typography.bodySmall)
                } else {
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        targets.forEach { n ->
                            FilterChip(target == n, { target = n },
                                leadingIcon = { Icon(Icons.Filled.Cloud, null, Modifier.size(16.dp)) },
                                label = { Text(n) })
                        }
                    }
                    OutlinedTextField(message, { message = it }, Modifier.fillMaxWidth(),
                        label = { Text("Auftrag") }, maxLines = 3)
                    OutlinedTextField(schedule, { schedule = it }, Modifier.fillMaxWidth(),
                        label = { Text("Zeitplan (leer = einmalig)") }, singleLine = true)
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("einmalig" to "", "täglich 8h" to "daily 08:00",
                               "stündlich" to "hourly", "alle 30m" to "every 30m").forEach { (lbl, v) ->
                            AssistChip(onClick = { schedule = v }, label = { Text(lbl) })
                        }
                    }
                    Button(
                        onClick = {
                            val m = message.trim()
                            if (m.isBlank() || target.isBlank()) return@Button
                            onStatus("Aufgabe wird angelegt…")
                            scope.launch {
                                val r = withContext(Dispatchers.IO) {
                                    ManagerSync.createTask(prefs.serverUrl, prefs.user, prefs.pass, target, m, schedule.trim())
                                }
                                message = ""; onStatus(r?.let { "✅ angelegt" } ?: "⚠️ ${ManagerSync.lastStatus}")
                                reload++
                            }
                        },
                        enabled = message.isNotBlank() && target.isNotBlank()
                    ) { Text(if (schedule.isBlank()) "Im Hintergrund starten" else "Zeitplan anlegen") }
                }
                HorizontalDivider(Modifier.padding(vertical = 4.dp))
                // --- Liste ---
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Laufende & erledigte", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
                    if (loading) CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                }
                if (tasks.isEmpty() && !loading) Text("Noch keine Aufgaben.", style = MaterialTheme.typography.bodySmall)
                tasks.forEach { t ->
                    Column(Modifier.fillMaxWidth()) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f).clickable { expanded = if (expanded == t.id) "" else t.id }) {
                                Text(t.message, style = MaterialTheme.typography.bodyMedium,
                                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                                val meta = buildString {
                                    append(statusLabel(t.status)); append(" · @${t.instance}")
                                    if (t.schedule.isNotBlank()) append(" · ${t.schedule}")
                                }
                                Text(meta, style = MaterialTheme.typography.labelSmall,
                                    color = if (t.status == "error") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            IconButton({
                                scope.launch {
                                    withContext(Dispatchers.IO) { ManagerSync.deleteTask(prefs.serverUrl, prefs.user, prefs.pass, t.id) }
                                    reload++
                                }
                            }) { Icon(Icons.Outlined.DeleteOutline, "Löschen") }
                        }
                        if (expanded == t.id && t.result.isNotBlank()) {
                            Surface(color = MaterialTheme.colorScheme.surfaceVariant,
                                shape = RectangleShape, modifier = Modifier.fillMaxWidth()) {
                                Text(t.result, Modifier.padding(10.dp), style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                }
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
