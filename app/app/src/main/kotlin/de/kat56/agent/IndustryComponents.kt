package de.kat56.agent

// Bausteine des Design-Systems "Industry" (claude.ai/design), uebersetzt aus
// styles.css. Bewusst nah am Original gehalten, damit App und Manager sich
// nicht auseinanderentwickeln.

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Blueprint-Rahmen: Haarlinie plus vier Registermarken, die — wie im
 * Original — *ausserhalb* der Box sitzen.
 *
 * In CSS ist eine Marke ein 11x11-Kaestchen, 6 px vor die Ecke geschoben, mit
 * einer senkrechten und einer waagerechten Haarlinie darin; das ergibt ein
 * kleines Kreuz mit der Ecke als Mittelpunkt. Compose clippt hier nicht, aber
 * die Marken brauchen trotzdem Platz: der Rahmen wird deshalb um 6 dp nach
 * innen gesetzt, damit die Arme innerhalb der eigenen Bounds bleiben und kein
 * Nachbar sie abschneidet.
 */
fun Modifier.blueprintFrame(border: Color, mark: Color): Modifier = this
    .padding(6.dp)
    .drawBehind {
        val line = 1.dp.toPx()
        val arm = 5.5.dp.toPx()
        drawRect(color = border, style = Stroke(width = line))
        val corners = listOf(
            Offset(0f, 0f), Offset(size.width, 0f),
            Offset(0f, size.height), Offset(size.width, size.height),
        )
        for (c in corners) {
            drawLine(mark, Offset(c.x, c.y - arm), Offset(c.x, c.y + arm), line)
            drawLine(mark, Offset(c.x - arm, c.y), Offset(c.x + arm, c.y), line)
        }
    }

/** Karte im Systemstil: transparent, Haarlinie, eckig, mit Registermarken. */
@Composable
fun BlueprintBox(
    modifier: Modifier = Modifier,
    contentPadding: PaddingValues = PaddingValues(IndustrySpacing.s3),
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier
            .blueprintFrame(Industry.divider, Industry.muted)
            .padding(contentPadding),
        content = content,
    )
}

/** .tag — 11 px, eckig, Paar aus Flaeche und Schrift. */
@Composable
fun Tag(text: String, accent: Boolean = false, modifier: Modifier = Modifier) {
    Box(
        modifier
            .background(if (accent) Industry.tagAccentBg else Industry.tagNeutralBg, RectangleShape)
            .padding(horizontal = IndustrySpacing.s3, vertical = 3.dp)
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelMedium.copy(fontSize = 11.sp),
            color = if (accent) Industry.tagAccentFg else Industry.tagNeutralFg,
        )
    }
}

/** .hr — Haarlinie als Trenner. */
@Composable
fun IndustryDivider(modifier: Modifier = Modifier) {
    Box(
        modifier
            .fillMaxWidth()
            .padding(vertical = IndustrySpacing.s4)
            .height(1.dp)
            .background(Industry.divider)
    )
}
