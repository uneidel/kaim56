package de.kat56.agent

// Angleichung an den Firecracker-Manager (Design-System "Industry"):
// Slate-Blau-Akzent, eckige Ecken, Barlow / Barlow Condensed. Ersetzt das
// bisherige Material-You-Dynamic-Color, damit die App wie der Manager aussieht.

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
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

// Ecken fast quadratisch — der Blueprint-Look des Managers.
val IndustryShapes = Shapes(
    extraSmall = RoundedCornerShape(0.dp),
    small = RoundedCornerShape(2.dp),
    medium = RoundedCornerShape(2.dp),
    large = RoundedCornerShape(3.dp),
    extraLarge = RoundedCornerShape(4.dp),
)

// Headings in Barlow Condensed, Body in Barlow.
val IndustryTypography = Typography().let { d ->
    d.copy(
        displayLarge = d.displayLarge.copy(fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold),
        displayMedium = d.displayMedium.copy(fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold),
        displaySmall = d.displaySmall.copy(fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold),
        headlineLarge = d.headlineLarge.copy(fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold),
        headlineMedium = d.headlineMedium.copy(fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold),
        headlineSmall = d.headlineSmall.copy(fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold),
        titleLarge = d.titleLarge.copy(fontFamily = BarlowCondensed, fontWeight = FontWeight.SemiBold, letterSpacing = 0.2.sp),
        titleMedium = d.titleMedium.copy(fontFamily = Barlow, fontWeight = FontWeight.SemiBold),
        titleSmall = d.titleSmall.copy(fontFamily = Barlow, fontWeight = FontWeight.SemiBold),
        bodyLarge = d.bodyLarge.copy(fontFamily = Barlow),
        bodyMedium = d.bodyMedium.copy(fontFamily = Barlow),
        bodySmall = d.bodySmall.copy(fontFamily = Barlow),
        labelLarge = d.labelLarge.copy(fontFamily = Barlow, fontWeight = FontWeight.Medium),
        labelMedium = d.labelMedium.copy(fontFamily = Barlow, fontWeight = FontWeight.Medium),
        labelSmall = d.labelSmall.copy(fontFamily = Barlow, fontWeight = FontWeight.Medium),
    )
}
