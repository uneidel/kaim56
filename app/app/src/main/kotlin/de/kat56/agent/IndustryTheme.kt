package de.kat56.agent

// Angleichung an den Firecracker-Manager (Design-System "Industry"):
// Slate-Blau-Akzent, eckige Ecken, Barlow / Barlow Condensed. Ersetzt das
// bisherige Material-You-Dynamic-Color, damit die App wie der Manager aussieht.

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private val Barlow = FontFamily(
    Font(R.font.barlow_regular, FontWeight.Normal),
    Font(R.font.barlow_medium, FontWeight.Medium),
    Font(R.font.barlow_semibold, FontWeight.SemiBold),
    Font(R.font.barlow_bold, FontWeight.Bold),
)
private val BarlowCondensed = FontFamily(Font(R.font.barlow_condensed_semibold, FontWeight.SemiBold))

// ── Industry-Tokens (siehe industry.css im Manager) ─────────────────────────
private val AccentLight = Color(0xFF5980A6)
private val Accent700Light = Color(0xFF416180)
private val AccentDark = Color(0xFF94BCE3)

val IndustryLight = lightColorScheme(
    primary = AccentLight,
    onPrimary = Color(0xFFF2F2F3),
    primaryContainer = Color(0xFFD6EBFF),
    onPrimaryContainer = Color(0xFF1D2D3D),
    secondary = Color(0xFF728FAB),
    onSecondary = Color(0xFFF2F2F3),
    background = Color(0xFFF2F2F3),
    onBackground = Color(0xFF1D1F20),
    surface = Color(0xFFE9E9EA),
    onSurface = Color(0xFF1D1F20),
    surfaceVariant = Color(0xFFE7E7EA),
    onSurfaceVariant = Color(0xFF5D5D60),
    outline = Color(0xFFB7B7BA),
    outlineVariant = Color(0xFFD4D4D7),
    error = Color(0xFFB3261E),
)

val IndustryDark = darkColorScheme(
    primary = AccentDark,
    onPrimary = Color(0xFF141618),
    primaryContainer = Color(0xFF2C455D),
    onPrimaryContainer = Color(0xFFD6EBFF),
    secondary = Color(0xFF9EBBD8),
    onSecondary = Color(0xFF141618),
    background = Color(0xFF141618),
    onBackground = Color(0xFFE8E9EA),
    surface = Color(0xFF1C1F22),
    onSurface = Color(0xFFE8E9EA),
    surfaceVariant = Color(0xFF2B2D31),
    onSurfaceVariant = Color(0xFFA9ACB1),
    outline = Color(0xFF4E5155),
    outlineVariant = Color(0xFF3A3D41),
    error = Color(0xFFF2B8B5),
)

// Eckig, ausnahmslos. styles.css setzt am Ende
//   .card, .btn, .input, .tag, .seg, .dialog { border-radius: 0 }
// und ueberstimmt damit das radius:4 aus theme.json — die Rundung gilt nur
// fuer Flaechen, die keine Komponente sind.
val IndustryShapes = Shapes(
    extraSmall = RoundedCornerShape(0.dp),
    small = RoundedCornerShape(0.dp),
    medium = RoundedCornerShape(0.dp),
    large = RoundedCornerShape(0.dp),
    extraLarge = RoundedCornerShape(0.dp),
)

/** Raster aus theme.json (density 0.85): 4/8/12/16/24/32 px * 0.85. */
object IndustrySpacing {
    val s1 = 3.4.dp
    val s2 = 6.8.dp
    val s3 = 10.2.dp
    val s4 = 13.6.dp
    val s6 = 20.4.dp
    val s8 = 27.2.dp
}

/**
 * Rollen, die Material3 nicht kennt, Industry aber sehr wohl: die Haarlinie
 * (Text auf 16 %), gedaempfter Text (55 %) und die Tag-Paare. Als Funktionen
 * ueber dem aktuellen Schema, damit sie in beiden Baendern stimmen.
 */
object Industry {
    val divider: Color @Composable get() = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.16f)
    val muted: Color @Composable get() = MaterialTheme.colorScheme.onBackground.copy(alpha = 0.55f)
    val tagAccentBg: Color @Composable get() = MaterialTheme.colorScheme.primaryContainer
    val tagAccentFg: Color @Composable get() = MaterialTheme.colorScheme.onPrimaryContainer
    val tagNeutralBg: Color @Composable get() = MaterialTheme.colorScheme.surfaceVariant
    val tagNeutralFg: Color @Composable get() = MaterialTheme.colorScheme.onSurfaceVariant
}

// Typo-Skala 1:1 aus styles.css: Headings in Barlow Condensed mit
// line-height 1.12 und letter-spacing -0.015em, Body in Barlow 15/1.55.
// h6 ist versal mit 0.08em Sperrung — das traegt im System die Kicker-Rolle.
private fun head(size: Int, lh: Double = 1.12) = TextStyle(
    fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold,
    fontSize = size.sp, lineHeight = (size * lh).sp, letterSpacing = (-0.015 * size).sp,
)

private fun body(size: Int, weight: FontWeight = FontWeight.Normal) = TextStyle(
    fontFamily = Barlow, fontWeight = weight,
    fontSize = size.sp, lineHeight = (size * 1.55).sp,
)

val IndustryTypography = Typography(
    displayLarge = head(42), displayMedium = head(32), displaySmall = head(25),
    headlineLarge = head(32), headlineMedium = head(25), headlineSmall = head(20),
    titleLarge = head(20), titleMedium = head(17), titleSmall = head(16),
    bodyLarge = body(15), bodyMedium = body(15), bodySmall = body(13),
    labelLarge = body(14, FontWeight.Medium), labelMedium = body(12, FontWeight.Medium),
    // labelSmall = h6: versal, gesperrt — der Kicker des Systems
    labelSmall = TextStyle(
        fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold,
        fontSize = 13.sp, lineHeight = 15.sp, letterSpacing = 1.04.sp,
    ),
)
