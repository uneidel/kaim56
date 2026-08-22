// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

// Colors, type and edges from the "KatAgent Prototype" (claude.ai/design).
//
// This prototype replaces the Industry tokens that stood here before. It is the
// newer template and was drawn explicitly for THIS app, so it wins wherever it
// conflicts:
//   Type      IBM Plex Sans / IBM Plex Mono   instead of Barlow / Barlow Condensed
//   Canvas    dark (#0E1218)                  instead of slate-on-light
//   Edges     rounded (10-22 px)              instead of consistently square
//   Frame     hairline without register marks instead of blueprint corners
//
// The prototype is defined in exactly ONE theme (dark="true", #0E1218). There is
// no light theme there — so the app always runs dark rather than inventing a
// second palette.

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

val Plex = FontFamily(
    Font(R.font.plex_sans_regular, FontWeight.Normal),
    Font(R.font.plex_sans_medium, FontWeight.Medium),
    Font(R.font.plex_sans_semibold, FontWeight.SemiBold),
    Font(R.font.plex_sans_bold, FontWeight.Bold),
)

val PlexMono = FontFamily(
    Font(R.font.plex_mono_regular, FontWeight.Normal),
    Font(R.font.plex_mono_medium, FontWeight.Medium),
)

/**
 * The prototype's color values, 1:1. Named by role, not by hue, so that
 * changes to the template affect exactly one place here.
 */
object Kat {
    val bg = Color(0xFF0E1218)            // screen background
    val surface = Color(0xFF171D26)       // card, menu, agent bubble, input
    val elevated = Color(0xFF12161D)      // drawer, attachment sheet
    val tile = Color(0xFF1B2430)          // avatar/icon tile, inactive send button
    val tileBorder = Color(0xFF26303E)
    val border = Color(0xFF262F3C)
    val borderFocus = Color(0xFF35507A)

    val accent = Color(0xFF2D5C96)        // filled action, own message
    val accentHover = Color(0xFF356BAD)
    val accentText = Color(0xFF7FB0E8)    // accent on a dark background
    val accentBright = Color(0xFFA9CBF2)
    val onAccent = Color(0xFFF0F5FB)
    val chipSel = Color(0xFF20304A)       // selected chip / selected preset
    val rowSel = Color(0xFF1B2836)        // open chat in the drawer

    val text = Color(0xFFE8ECF2)
    val textStrong = Color(0xFFDCE3EC)
    val textDim = Color(0xFFB7C0CE)
    val textMuted = Color(0xFF8A94A6)
    val textFaint = Color(0xFF7A8598)
    val textSubtle = Color(0xFF5C6879)
    val textGhost = Color(0xFF3D4655)

    val green = Color(0xFF4CC38A)
    val red = Color(0xFFE06C75)

    // rgba(255,255,255,a) from the template
    val hairline = Color(0x0FFFFFFF)      // 0.06
    val hairlineStrong = Color(0x12FFFFFF) // 0.07
    val hover = Color(0x0DFFFFFF)         // 0.05
    val wash = Color(0x0AFFFFFF)          // 0.04
    val scrim = Color(0x80000000)         // 0.5

    /**
     * Agent colors from the template. Unknown names stay neutral — exactly
     * like in the prototype (`agentColor[c.agent] || '#8A94A6'`).
     */
    fun agent(name: String): Color = when (name) {
        "orchestrator" -> Color(0xFF7FB0E8)
        "hass" -> Color(0xFF5BC4B0)
        "ephemeral" -> Color(0xFFC9A15B)
        "research" -> Color(0xFFB58BE8)
        else -> textMuted
    }
}

val KatColors = darkColorScheme(
    primary = Kat.accent,
    onPrimary = Kat.onAccent,
    primaryContainer = Kat.chipSel,
    onPrimaryContainer = Kat.accentBright,
    secondary = Kat.accentText,
    onSecondary = Kat.bg,
    background = Kat.bg,
    onBackground = Kat.text,
    surface = Kat.surface,
    onSurface = Kat.textStrong,
    surfaceVariant = Kat.tile,
    onSurfaceVariant = Kat.textFaint,
    outline = Kat.border,
    outlineVariant = Kat.hairlineStrong,
    error = Kat.red,
    onError = Kat.bg,
)

/**
 * The radii the prototype actually names: 10 px on input fields and menu rows,
 * 14 px on cards, 18 px on chips, 22 px on the round buttons.
 */
val KatShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(10.dp),
    medium = RoundedCornerShape(14.dp),
    large = RoundedCornerShape(18.dp),
    extraLarge = RoundedCornerShape(22.dp),
)

private fun sans(size: Double, weight: FontWeight = FontWeight.Normal, lh: Double = 1.4, ls: Double = 0.0) =
    TextStyle(
        fontFamily = Plex, fontWeight = weight,
        fontSize = size.sp, lineHeight = (size * lh).sp, letterSpacing = ls.sp,
    )

/**
 * The sizes as given in the template: 17/600 in the header, 15 in the bubble,
 * 14.5 in list rows, 13.5 on chips, 12 on subrows, 11 in the footnote.
 * 11.5/600 uppercase with 0.08em tracking is the section title.
 */
val KatTypography = Typography(
    displayLarge = sans(32.0, FontWeight.SemiBold, 1.15, -0.32),
    displayMedium = sans(25.0, FontWeight.SemiBold, 1.15, -0.25),
    displaySmall = sans(20.0, FontWeight.SemiBold, 1.2, -0.2),
    headlineLarge = sans(25.0, FontWeight.SemiBold, 1.2, -0.25),
    headlineMedium = sans(20.0, FontWeight.SemiBold, 1.2, -0.2),
    headlineSmall = sans(18.0, FontWeight.SemiBold, 1.25, -0.18),
    titleLarge = sans(18.0, FontWeight.SemiBold, 1.3, -0.18),
    titleMedium = sans(17.0, FontWeight.SemiBold, 1.3, -0.17),
    titleSmall = sans(14.5, FontWeight.Medium, 1.35),
    bodyLarge = sans(15.0, FontWeight.Normal, 1.5),
    bodyMedium = sans(14.0, FontWeight.Normal, 1.45),
    bodySmall = sans(13.0, FontWeight.Normal, 1.45),
    labelLarge = sans(14.0, FontWeight.SemiBold, 1.3),
    labelMedium = sans(12.0, FontWeight.Normal, 1.35),
    // section title: uppercase, letter-spaced
    labelSmall = sans(11.5, FontWeight.SemiBold, 1.3, 0.92),
)
