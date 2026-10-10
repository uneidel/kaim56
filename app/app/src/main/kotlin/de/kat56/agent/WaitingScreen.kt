// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.automirrored.outlined.HelpOutline
import androidx.compose.material.icons.outlined.RateReview
import androidx.compose.material3.Icon
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Approvals, questions and reviews agents are blocked on, grouped by
 *  project/squad. Reloads on open, when [refresh] moves (a waiting
 *  notification arrived) and every 30 s while shown; every load reports the
 *  open count to [onCount] for the drawer badge. */
@Composable
internal fun WaitingScreen(prefs: Prefs, refresh: Int, onClose: () -> Unit, onCount: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    val snack = remember { SnackbarHostState() }
    var items by remember { mutableStateOf<List<WaitingItem>>(emptyList()) }
    var loaded by remember { mutableStateOf(false) }
    var reload by remember { mutableStateOf(0) }
    var busy by remember { mutableStateOf("") }   // id being decided

    LaunchedEffect(reload, refresh) {
        val wc = WaitingClient(prefs)   // its own client: the error belongs to this call
        val r = withContext(Dispatchers.IO) { wc.open() }
        if (r != null) { items = r; loaded = true; onCount(WaitingClient.count(r)) }
        else snack.showSnackbar("⚠️ Could not load: ${wc.lastError}")
    }
    LaunchedEffect(Unit) { while (true) { delay(30000); reload++ } }

    fun decide(it: WaitingItem, action: String, text: String, choice: String = "") {
        WaitingClient.check(it.kind, action, text, choice)?.let { msg -> scope.launch { snack.showSnackbar(msg) }; return }
        busy = it.id
        scope.launch {
            val r = withContext(Dispatchers.IO) { WaitingClient(prefs).act(it.id, action, text, choice) }
            busy = ""
            reload++
            snack.showSnackbar(when (r) {
                is WaitingClient.Outcome.Done -> when (action) {
                    "approve" -> "Approved: ${it.title}"
                    "deny" -> "Denied: ${it.title}"
                    "request_changes" -> "Changes requested: ${it.title}"
                    else -> "Answered: ${it.title}"
                }
                is WaitingClient.Outcome.AlreadyDecided -> "Already decided elsewhere — list refreshed"
                is WaitingClient.Outcome.Failed -> "⚠️ ${r.msg}"
            })
        }
    }

    Box(Modifier.fillMaxSize().background(Kat.bg)) {
        Column(Modifier.fillMaxSize()) {
            ScreenHeader("Waiting for you", onClose)
            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (loaded && items.isEmpty()) Text(
                    "Nothing is waiting for you. When an agent needs an approval, an answer " +
                    "or a review, it shows up here and as a notification.",
                    fontSize = 13.sp, fontFamily = Plex, color = Kat.textFaint,
                )
                items.groupBy { it.group }.forEach { (group, list) ->
                    Kicker(group, Modifier.padding(top = 6.dp))
                    list.forEach { w -> WaitingCard(w, busy == w.id, ::decide) }
                }
            }
        }
        SnackbarHost(snack, Modifier.align(Alignment.BottomCenter).padding(16.dp))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WaitingCard(w: WaitingItem, busy: Boolean, decide: (WaitingItem, String, String, String) -> Unit) {
    var expanded by remember(w.id) { mutableStateOf(false) }
    var text by remember(w.id) { mutableStateOf("") }
    val (icon, label) = when (w.kind) {
        "approval" -> Icons.Outlined.CheckCircle to "Approval"
        "question" -> Icons.AutoMirrored.Outlined.HelpOutline to "Question"
        "review" -> Icons.Outlined.RateReview to "Review"
        else -> Icons.AutoMirrored.Outlined.HelpOutline to w.kind.ifBlank { "Item" }
    }
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp))
            .background(Kat.surface).border(1.dp, Kat.hairlineStrong, RoundedCornerShape(14.dp))
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(icon, null, Modifier.size(16.dp), tint = Kat.accentText)
            Text(label, fontSize = 11.sp, fontFamily = Plex, fontWeight = FontWeight.SemiBold, color = Kat.accentText)
            if (w.ticket.isNotBlank()) Text(w.ticket, fontSize = 11.sp, fontFamily = PlexMono, color = Kat.textFaint)
            Spacer(Modifier.weight(1f))
            if (w.instance.isNotBlank())
                Text(w.instance, fontSize = 11.sp, fontFamily = PlexMono, color = Kat.agent(w.instance))
        }
        Text(w.title.ifBlank { "(untitled)" }, fontSize = 14.5.sp, fontFamily = Plex, fontWeight = FontWeight.Medium,
            color = Kat.text)
        if (w.tool.isNotBlank() || w.target.isNotBlank())
            Text(listOf(w.tool, w.target).filter { it.isNotBlank() }.joinToString(" → "),
                fontSize = 11.5.sp, fontFamily = PlexMono, color = Kat.textMuted, maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (w.detail.isNotBlank()) Text(
            w.detail, fontSize = 12.5.sp, fontFamily = Plex, color = Kat.textDim,
            maxLines = if (expanded) Int.MAX_VALUE else 3, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.tap { expanded = !expanded },
        )
        if (w.kind == "question" && w.options.isNotEmpty()) FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            w.options.forEach { o ->
                Text(o, fontSize = 12.5.sp, fontFamily = Plex, color = Kat.accentBright,
                    modifier = Modifier.clip(RoundedCornerShape(16.dp)).background(Kat.chipSel)
                        .tap(!busy) { decide(w, "answer", text, o) }
                        .padding(horizontal = 12.dp, vertical = 6.dp))
            }
        }
        val actions = WaitingClient.actionsFor(w.kind)
        if (actions.isNotEmpty()) {
            KatField(text, { text = it }, placeholder = when (w.kind) {
                "approval" -> "Comment (optional)"
                "review" -> "Comment (required to request changes)"
                else -> if (w.options.isEmpty()) "Your answer" else "Or type an answer"
            })
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                actions.forEachIndexed { i, a ->
                    val name = when (a) {
                        "approve" -> "Approve"; "deny" -> "Deny"
                        "request_changes" -> "Request changes"; else -> "Answer"
                    }
                    if (i == 0) FilledPill(name, { decide(w, a, text, "") }, height = 38.dp, enabled = !busy)
                    else OutlinePill(name, { decide(w, a, text, "") }, height = 38.dp, enabled = !busy)
                }
            }
        }
    }
}
