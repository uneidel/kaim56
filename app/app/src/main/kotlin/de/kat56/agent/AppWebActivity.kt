// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Base64
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.net.HttpURLConnection

/**
 * One of the manager's apps (/apps/<name>/) in a WebView.
 *
 * The page lives on a virtual origin (https://kaim56.app); every request to it
 * is made here, with the manager's login, against the configured server URL —
 * so it works the same over http(s) and over iroh:// (a WebView cannot load
 * iroh:// itself). GETs go through shouldInterceptRequest; fetch() calls with
 * a body come through the KaimBridge interface (AppsCatalog.FETCH_SHIM).
 * Links to anywhere else open in the browser.
 */
class AppWebActivity : Activity() {

    private lateinit var web: WebView
    private lateinit var prefs: Prefs

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val name = intent.getStringExtra("app") ?: run { finish(); return }
        title = intent.getStringExtra("title") ?: name
        prefs = Prefs(this)
        IrohNet.register(this)   // in case the process started here (iroh:// server URL)

        web = WebView(this).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            webChromeClient = WebChromeClient()
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                    if (req.url.host == HOST) return false
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, req.url)) }
                    return true
                }
                override fun shouldInterceptRequest(view: WebView, req: WebResourceRequest): WebResourceResponse? {
                    if (req.url.host != HOST) return null
                    return fetchGet(req.url)
                }
            }
            addJavascriptInterface(Bridge(), "KaimBridge")
        }
        setContentView(web)
        web.loadUrl("https://$HOST/apps/${Uri.encode(name)}/")
    }

    private fun open(path: String, method: String): HttpURLConnection =
        ManagerClient(prefs).connect(path, method, 60000).apply { instanceFollowRedirects = false }

    private fun page(code: Int, html: String) = WebResourceResponse(
        "text/html", "utf-8", code, if (code < 400) "OK" else "Error",
        mapOf("Cache-Control" to "no-store"), ByteArrayInputStream(html.toByteArray()))

    /** A GET on the virtual origin, against the manager. Redirects within the
     *  manager are followed here (a WebResourceResponse cannot be a 3xx); one
     *  to elsewhere — an app moved to Cloudflare — becomes a link. */
    private fun fetchGet(uri: Uri): WebResourceResponse {
        var path = (uri.encodedPath ?: "/") + (uri.encodedQuery?.let { "?$it" } ?: "")
        try {
            repeat(5) {
                val conn = open(path, "GET")
                val code = conn.responseCode
                if (code in 300..399) {
                    val loc = conn.getHeaderField("Location").orEmpty()
                    conn.disconnect()
                    val u = Uri.parse(loc)
                    if (u.scheme == null || u.host == null) { path = if (loc.startsWith("/")) loc else "/$loc"; return@repeat }
                    val esc = loc.replace("&", "&amp;").replace("<", "&lt;").replace("\"", "&quot;")
                    return page(200, "<!doctype html><meta name=viewport content='width=device-width'>" +
                        "<body style='font-family:sans-serif;background:#0B0E13;color:#E8ECF2;padding:24px'>" +
                        "<p>This app runs on Cloudflare now.</p><p><a style='color:#7FB0E8' href=\"$esc\">Open $esc</a></p>")
                }
                val type = conn.contentType.orEmpty()
                val mime = type.substringBefore(';').trim().ifBlank { "application/octet-stream" }
                val charset = Regex("charset=([^;]+)", RegexOption.IGNORE_CASE).find(type)?.groupValues?.get(1)?.trim() ?: "utf-8"
                val stream = (if (code >= 400) conn.errorStream else conn.inputStream) ?: ByteArrayInputStream(ByteArray(0))
                val headers = mapOf("Cache-Control" to "no-store")
                val reason = conn.responseMessage?.ifBlank { null } ?: if (code < 400) "OK" else "Error"
                if (mime == "text/html") {
                    val html = stream.use { it.readBytes().toString(Charsets.UTF_8) }
                    return WebResourceResponse(mime, "utf-8", code, reason, headers,
                        ByteArrayInputStream(AppsCatalog.inject(html).toByteArray()))
                }
                return WebResourceResponse(mime, charset, code, reason, headers, stream)
            }
            return page(508, "<p>Too many redirects.</p>")
        } catch (e: Exception) {
            return page(502, "<body style='font-family:sans-serif;padding:24px'><p>Manager not reachable: ${e.message?.replace("<", "&lt;")}</p>")
        }
    }

    inner class Bridge {
        /** fetch() with a body, from FETCH_SHIM; answers via __kaimDone. */
        @JavascriptInterface
        fun request(id: Int, method: String, path: String, contentType: String, bodyB64: String) {
            Thread {
                var code = 0; var ct = ""; var out = ""
                try {
                    require(path.startsWith("/")) { "bad path" }
                    val conn = open(path, method.uppercase())
                    val body = Base64.decode(bodyB64, Base64.DEFAULT)
                    if (body.isNotEmpty() || method.uppercase() in setOf("POST", "PUT", "PATCH")) {
                        conn.doOutput = true
                        if (contentType.isNotBlank()) conn.setRequestProperty("Content-Type", contentType)
                        conn.outputStream.use { it.write(body) }
                    }
                    code = conn.responseCode
                    ct = conn.contentType.orEmpty()
                    val bytes = ((if (code >= 400) conn.errorStream else conn.inputStream))?.use { it.readBytes() } ?: ByteArray(0)
                    out = Base64.encodeToString(bytes, Base64.NO_WRAP)
                } catch (e: Exception) {
                    code = 0; out = e.message ?: "network error"
                }
                val js = "window.__kaimDone($id,$code,${JSONObject.quote(ct)},${JSONObject.quote(out)})"
                web.post { web.evaluateJavascript(js, null) }
            }.start()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }

    companion object { const val HOST = "kaim56.app" }
}
