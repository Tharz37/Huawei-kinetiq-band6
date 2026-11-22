package com.kinetiq.streamer

import android.app.Notification
import android.os.SystemClock
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

/**
 * Second extraction track: reads Huawei Health's status-bar notification.
 *
 * This catches Huawei Health's ongoing real-time pedometer/heart-rate update
 * while the Health app is in the background. It is deliberately a supplement,
 * not a replacement, for the accessibility watchdog:
 *  - it only parses text from notifications posted by com.huawei.health;
 *  - it refuses to POST a steps-only packet (the receiver's genuine-slot
 *    contract requires at least one of HR/SpO2/stress);
 *  - it defers to the accessibility service when Health was foreground within
 *    the last few seconds, so the same on-screen reading is not posted twice;
 *  - it shares [HealthRelay]'s POST gate and wire contract.
 *
 * The user must grant notification access explicitly (Settings > Notifications
 * > Device & app notifications > KinetiQ Streamer). The install script can
 * make that grant through the shell on a debuggable demo phone.
 */
class HealthNotificationService : NotificationListenerService() {

    private lateinit var prefs: Prefs

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        Log.i(TAG, "Health notification listener attached")
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        Log.i(TAG, "Health notification listener connected")
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null || !::prefs.isInitialized || !prefs.enabled) return
        if (sbn.packageName != TARGET_PACKAGE) return

        val notification = sbn.notification ?: return
        val extras = notification.extras ?: return

        val nodes = buildList {
            val keys = listOf(
                Notification.EXTRA_TITLE,
                Notification.EXTRA_TEXT,
                Notification.EXTRA_SUB_TEXT,
                Notification.EXTRA_BIG_TEXT,
                Notification.EXTRA_SUMMARY_TEXT,
                Notification.EXTRA_INFO_TEXT
            )
            for (key in keys) {
                val value = extras.getCharSequence(key)?.toString()
                if (!value.isNullOrBlank()) add(value)
            }
            val lines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES)
            if (lines != null) {
                for (line in lines) {
                    val value = line?.toString()
                    if (!value.isNullOrBlank()) add(value)
                }
            }
        }
        if (nodes.isEmpty()) return

        val reading = HealthParser.parse(nodes)
        if (!reading.hasVitals) {
            Log.i(
                TAG,
                "Notification ${sbn.key} had no HR/SpO2/stress value: ${reading.summary()}"
            )
            return
        }

        val now = SystemClock.elapsedRealtime()
        if (StreamService.isConnected &&
            StreamService.lastHealthForegroundAt != 0L &&
            now - StreamService.lastHealthForegroundAt < HealthRelay.FOREGROUND_GRACE_MS
        ) {
            Log.i(
                TAG,
                "Accessibility watchdog is reading Health; skipping notification duplicate: " +
                    reading.summary()
            )
            return
        }

        if (!HealthRelay.tryAcquire(reading.summary(), now)) {
            Log.i(
                TAG,
                "Notification POST gated (minimum interval or unchanged reading): " +
                    reading.summary()
            )
            return
        }
        send(reading)
    }

    private fun send(reading: HealthParser.Reading) {
        HealthRelay.post(prefs.endpoint, prefs.token, reading, TAG) { result, values, time ->
            prefs.recordPost(result, values, time)
            StreamService.lastResult = result
            StreamService.lastPostedValues = values
            StreamService.lastPostTimeMillis = time
        }
    }

    companion object {
        const val TAG = "KinetiQ-Notif"
        const val TARGET_PACKAGE = StreamService.TARGET_PACKAGE
    }
}
