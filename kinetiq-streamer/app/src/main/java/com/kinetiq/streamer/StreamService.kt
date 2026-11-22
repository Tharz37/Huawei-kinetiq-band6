package com.kinetiq.streamer

import android.accessibilityservice.AccessibilityService
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Watches the Huawei Health UI and forwards genuinely displayed readings to
 * the local KinetiQ receiver.
 *
 * HONESTY RULES (hard requirements)
 *  - Only nodes of com.huawei.health are read; the root window is re-checked
 *    even though accessibility_config.xml already filters by package.
 *  - Nothing is synthesised: a metric absent from the screen is serialised as
 *    JSON null (never 0), and no POST happens unless at least one vital
 *    (HR/SpO2/stress) was parsed. Steps-only packets are refused because the
 *    receiver's genuine-slot contract requires at least one vital.
 *  - source / transport / simulated are fixed in [HealthRelay] and match
 *    backend/kinetiq_receiver.py's health-app relay path.
 *
 * Scheduling: an active watchdog inspects the screen every 4 s while the screen
 * is interactive. Accessibility events still trigger a faster settle-and-parse
 * path. Both are throttled to one parse per [POLLING_INTERVAL_MS]. POSTs are
 * gated process-wide by [HealthRelay] so the notification listener cannot
 * create a duplicate row for the same reading.
 */
class StreamService : AccessibilityService() {

    private lateinit var prefs: Prefs
    private val handler = Handler(Looper.getMainLooper())

    private var parseScheduled = false
    private var lastParseAt = 0L

    private val parseRunnable = Runnable {
        parseScheduled = false
        parseOnce()
    }

    /**
     * Active screen watchdog. It does not require a new accessibility event;
     * it re-reads the visible Health screen every 4 s. When another app is in
     * front, parseOnce() still checks the root package and returns without
     * reading foreign content.
     */
    private val pollRunnable = object : Runnable {
        override fun run() {
            if (::prefs.isInitialized && prefs.enabled && isScreenInteractive()) {
                scheduleParse(0L)
            }
            handler.postDelayed(this, POLLING_INTERVAL_MS)
        }
    }

    // ------------------------------------------------------------------ lifecycle

    override fun onServiceConnected() {
        super.onServiceConnected()
        prefs = Prefs(this)
        isConnected = true
        lastResult = prefs.lastResult
        lastPostedValues = prefs.lastPostedValues
        lastPostTimeMillis = prefs.lastPostTime
        Log.i(TAG, "Accessibility service connected; enabled=${prefs.enabled}")

        handler.removeCallbacks(pollRunnable)
        handler.postDelayed(pollRunnable, FIRST_POLL_DELAY_MS)
        if (prefs.enabled) scheduleParse(FIRST_PARSE_DELAY_MS)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (!::prefs.isInitialized || !prefs.enabled) return
        val packageName = event.packageName?.toString()
        if (packageName != null && packageName != TARGET_PACKAGE) return
        scheduleParse(SETTLE_DELAY_MS)
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
        handler.removeCallbacks(parseRunnable)
        parseScheduled = false
    }

    override fun onDestroy() {
        handler.removeCallbacks(parseRunnable)
        handler.removeCallbacks(pollRunnable)
        isConnected = false
        lastHealthForegroundAt = 0L
        Log.i(TAG, "Accessibility service destroyed")
        super.onDestroy()
    }

    // ------------------------------------------------------------------ throttle

    private fun isScreenInteractive(): Boolean {
        val powerManager = getSystemService(PowerManager::class.java) ?: return true
        return powerManager.isInteractive
    }

    private fun scheduleParse(delayMs: Long) {
        if (!::prefs.isInitialized || !prefs.enabled) return
        if (parseScheduled) return
        val sinceLastParse = SystemClock.elapsedRealtime() - lastParseAt
        if (lastParseAt != 0L && sinceLastParse < POLLING_INTERVAL_MS) return
        parseScheduled = true
        handler.postDelayed(parseRunnable, delayMs)
    }

    // ------------------------------------------------------------------ parsing

    private fun parseOnce() {
        if (!::prefs.isInitialized || !prefs.enabled) return

        val root = rootInActiveWindow
        if (root == null) {
            Log.d(TAG, "No active window; nothing parsed")
            return
        }
        val rootPackage = root.packageName?.toString()
        if (rootPackage != TARGET_PACKAGE) {
            // Another app is in front; do not touch its content and do not
            // start the parse throttle so returning to Health is immediate.
            Log.d(TAG, "Active window belongs to ${rootPackage ?: "unknown"}; ignoring")
            return
        }

        lastParseAt = SystemClock.elapsedRealtime()
        lastHealthForegroundAt = lastParseAt
        val texts = collectTexts(root)
        val reading = HealthParser.parse(texts)
        lastParsedSummary = reading.summary()
        Log.i(TAG, "Parsed ${texts.size} text nodes -> ${reading.summary()}")

        if (!reading.hasVitals) {
            Log.i(TAG, "No HR/SpO2/stress reading on the Health screen; not posting")
            return
        }
        maybePost(reading)
    }

    /**
     * Depth-first text collection. Falls back to contentDescription when a
     * node has no text (Health uses it for some tiles).
     */
    private fun collectTexts(root: AccessibilityNodeInfo): List<String> {
        val out = ArrayList<String>(64)
        walk(root, out, 0)
        return out
    }

    private fun walk(node: AccessibilityNodeInfo, out: MutableList<String>, depth: Int) {
        if (depth > MAX_DEPTH || out.size >= MAX_NODES) return

        val text = node.text?.toString()
        val chosen = if (!text.isNullOrBlank()) text else node.contentDescription?.toString()
        if (!chosen.isNullOrBlank()) out.add(chosen)

        var index = 0
        while (index < node.childCount && out.size < MAX_NODES) {
            val child = try {
                node.getChild(index)
            } catch (e: IllegalStateException) {
                Log.d(TAG, "Child $index unavailable: ${e.message}")
                null
            }
            if (child != null) walk(child, out, depth + 1)
            index++
        }
        // Recycle is a no-op on API 33+ and deprecated; skipping it avoids
        // double-recycle crashes while the tree is still referenced.
    }

    // ------------------------------------------------------------------ posting

    private fun maybePost(reading: HealthParser.Reading) {
        val now = SystemClock.elapsedRealtime()
        if (!HealthRelay.tryAcquire(reading.summary(), now)) {
            Log.i(
                TAG,
                "POST gated (minimum ${HealthRelay.MIN_POST_INTERVAL_MS} ms or unchanged " +
                    "reading ${HealthRelay.SAME_READING_HEARTBEAT_MS} ms)"
            )
            return
        }
        send(reading)
    }

    private fun send(reading: HealthParser.Reading) {
        HealthRelay.post(prefs.endpoint, prefs.token, reading, TAG) { result, values, time ->
            prefs.recordPost(result, values, time)
            lastResult = result
            lastPostedValues = values
            lastPostTimeMillis = time
        }
    }

    companion object {
        const val TAG = "KinetiQ-Stream"
        const val TARGET_PACKAGE = "com.huawei.health"

        /** Active watchdog / parse interval while Health is visible. */
        const val POLLING_INTERVAL_MS = 4_000L

        private const val FIRST_POLL_DELAY_MS = 1_000L
        private const val FIRST_PARSE_DELAY_MS = 600L
        private const val SETTLE_DELAY_MS = 350L
        private const val MAX_DEPTH = 48
        private const val MAX_NODES = 2000

        // In-process mirror of the last POST so MainActivity can refresh
        // instantly; SharedPreferences is the durable copy.
        @Volatile
        var isConnected: Boolean = false
        @Volatile
        var lastResult: String? = null
        @Volatile
        var lastPostedValues: String? = null
        @Volatile
        var lastPostTimeMillis: Long = 0L
        @Volatile
        var lastParsedSummary: String? = null

        /**
         * Monotonic timestamp of the last successful Health foreground parse.
         * The notification listener uses this to avoid duplicating the same
         * on-screen reading.
         */
        @Volatile
        var lastHealthForegroundAt: Long = 0L
    }
}
