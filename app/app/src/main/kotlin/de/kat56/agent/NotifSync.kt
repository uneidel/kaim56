// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
package de.kat56.agent

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * Notifications from the manager reach the phone on two paths:
 *
 *  * while the app is open, MainActivity long-polls /api/notifications and
 *    hands every answer to [handle];
 *  * while it is closed, [NotifWorker] (WorkManager, every 15 min, network
 *    required) fetches the list once and hands it to [handle] too.
 *
 * Both share ONE watermark in the prefs, the timestamp of the newest entry
 * already shown ([Prefs.notifLastTs]). Before 5.42 the app remembered only
 * "since app start", so a digest that arrived while the app was closed was
 * never shown — not even at the next start.
 */
object NotifSync {
    const val WORK = "kaim56-notifications"
    /** First run on a device: how far back unread entries are still shown. */
    const val FIRST_RUN_WINDOW_S = 24 * 3600L

    /** Which entries to show now: unread, newer than the watermark; on the
     *  very first run (no watermark) only the last day, so an old backlog does
     *  not flood the shade. Pure, so it is unit-tested. */
    fun pick(items: List<ManagerSync.NotifItem>, lastTs: Long, nowS: Long): List<ManagerSync.NotifItem> {
        val floor = if (lastTs > 0) lastTs else nowS - FIRST_RUN_WINDOW_S
        return items.filter { !it.read && it.ts > floor }.sortedBy { it.ts }
    }

    /** Show what is new and move the watermark. Returns how many were shown. */
    @Synchronized
    fun handle(ctx: Context, prefs: Prefs, items: List<ManagerSync.NotifItem>?,
               nowS: Long = System.currentTimeMillis() / 1000): Int {
        if (items == null) return 0
        val fresh = pick(items, prefs.notifLastTs, nowS)
        fresh.forEach { showAgentNotification(ctx, it.id, it.title, it.body, it.link) }
        val newest = items.maxOfOrNull { it.ts } ?: 0L
        // The watermark also advances over READ entries: what was read on the
        // web is not shown here later either.
        if (newest > prefs.notifLastTs) prefs.notifLastTs = newest
        if (prefs.notifLastTs == 0L) prefs.notifLastTs = nowS      // first run, empty list: from now on
        return fresh.size
    }

    /** One fetch (no long-poll) + handle. Used by the worker and at app start. */
    fun checkOnce(ctx: Context, prefs: Prefs): Int {
        if (prefs.serverUrl.isBlank()) return 0
        IrohNet.register(ctx)
        val res = ManagerSync.pollNotifications(prefs.serverUrl, prefs.user, prefs.pass, 0, 0)
            ?: return 0
        return handle(ctx, prefs, res.items)
    }

    /** Schedule the background check; KEEP = an existing schedule stays. */
    fun schedule(ctx: Context) {
        val req = PeriodicWorkRequestBuilder<NotifWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(ctx.applicationContext)
            .enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, req)
    }
}

class NotifWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        runCatching { NotifSync.checkOnce(applicationContext, Prefs(applicationContext)) }
        return Result.success()          // a failed fetch is retried at the next period anyway
    }
}
