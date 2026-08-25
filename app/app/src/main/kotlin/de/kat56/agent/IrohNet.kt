// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// iroh transport for the app. Registers an `iroh://` URL scheme so every
// existing HttpURLConnection call site keeps working unchanged: the base URL is
// `iroh://<manager-node-id>` and requests are tunnelled over iroh (P2P, no VPN,
// no HTTPS endpoint) to the manager gateway. Backed by the kaim_iroh native
// module (UniFFI).
package de.kat56.agent

import android.content.Context
import uniffi.kaim_iroh.IrohClient
import java.io.File
import java.net.URL
import java.net.URLStreamHandler
import java.net.URLStreamHandlerFactory

object IrohNet {
    @Volatile private var client: IrohClient? = null
    @Volatile private var registered = false

    /** Diagnostics to the app's EXTERNAL files dir (readable via a file manager
     *  at Android/data/de.kat56.agent/files/iroh-debug.log — no adb needed). A
     *  breadcrumb before each native step, so even a hard native crash leaves a
     *  trail showing how far it got. */
    fun crumb(ctx: Context, msg: String) {
        try {
            val dir = ctx.getExternalFilesDir(null) ?: ctx.filesDir
            File(dir, "iroh-debug.log").appendText("${System.currentTimeMillis()} $msg\n")
        } catch (_: Throwable) {}
    }

    /** The one iroh endpoint for this app (stable node-id, persisted key).
     *  Created lazily and NEVER on the main thread (callers use IO threads). */
    fun client(ctx: Context): IrohClient {
        client?.let { return it }
        return synchronized(this) {
            client ?: run {
                crumb(ctx, "IrohClient: creating (bind endpoint)…")
                val c = IrohClient(File(ctx.filesDir, "iroh-node.key").absolutePath)
                crumb(ctx, "IrohClient: created, node_id=${runCatching { c.nodeId() }.getOrDefault("?")}")
                client = c
                c
            }
        }
    }

    /** This device's node-id — paste into the manager allowlist to pair.
     *  MUST be called off the main thread (it may create the endpoint). */
    fun myNodeId(ctx: Context): String =
        runCatching { client(ctx).nodeId() }.getOrDefault("")

    /** Install the `iroh://` scheme once. Does NOT create the endpoint (that is
     *  lazy, on first request, off the main thread). Safe to call repeatedly. */
    fun register(ctx: Context) {
        if (registered) return
        val app = ctx.applicationContext
        crumb(app, "register: installing iroh:// URL handler")
        try {
            URL.setURLStreamHandlerFactory(object : URLStreamHandlerFactory {
                override fun createURLStreamHandler(protocol: String): URLStreamHandler? =
                    if (protocol == "iroh") object : URLStreamHandler() {
                        override fun openConnection(u: URL) = IrohUrlConnection(u, app)
                    } else null
            })
        } catch (e: Throwable) {
            // setURLStreamHandlerFactory can only be called once per JVM; if the
            // host already set one, ours may already be in place. Never fatal.
            crumb(app, "register: factory already set (${e.javaClass.simpleName})")
        }
        registered = true
    }

    /** Build the base URL the call sites use from a manager node-id. */
    fun baseUrl(managerNodeId: String): String = "iroh://" + managerNodeId.trim().lowercase()
}
