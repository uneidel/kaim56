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

    /** The one iroh endpoint for this app (stable node-id, persisted key). */
    fun client(ctx: Context): IrohClient {
        client?.let { return it }
        return synchronized(this) {
            client ?: IrohClient(File(ctx.filesDir, "iroh-node.key").absolutePath).also { client = it }
        }
    }

    /** This device's node-id — paste into the manager allowlist to pair. */
    fun myNodeId(ctx: Context): String =
        runCatching { client(ctx).nodeId() }.getOrDefault("")

    /** Install the `iroh://` scheme once. Safe to call repeatedly. */
    fun register(ctx: Context) {
        if (registered) return
        val app = ctx.applicationContext
        // Warm the endpoint up front (binding takes a moment) and pre-touch the id.
        runCatching { client(app) }
        try {
            URL.setURLStreamHandlerFactory(object : URLStreamHandlerFactory {
                override fun createURLStreamHandler(protocol: String): URLStreamHandler? =
                    if (protocol == "iroh") object : URLStreamHandler() {
                        override fun openConnection(u: URL) = IrohUrlConnection(u, app)
                    } else null
            })
        } catch (e: Error) {
            // setURLStreamHandlerFactory can only be called once per JVM; if the
            // host already set one, that's fine — ours may already be in place.
        }
        registered = true
    }

    /** Build the base URL the call sites use from a manager node-id. */
    fun baseUrl(managerNodeId: String): String = "iroh://" + managerNodeId.trim().lowercase()
}
