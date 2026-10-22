package com.kinetiq.streamer

import android.accessibilityservice.AccessibilityService
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Watches the Huawei Health UI and forwards genuinely displayed readings to
 * the local KinetiQ receiver.
 *
 * HONESTY RULES (hard requirements)
 *  - Only nodes of com.huawei.health are read; the root window is re-checked
 *    even though accessibility_config.xml already filters by package.
 *  - Nothing is synthesised: a metric absent from the screen is serialised as
 *    JSON null (never 0), and no POST happens unless at least one metric was
 *    parsed.
 *  - source / transport / simulated are constants and match
 *    backend/kinetiq_receiver.py's health-app relay path.
 *
 * Throttling: parse at most once per [PARSE_INTERVAL_MS], POST at most once
 * per [POST_INTERVAL_MS]. Both use the monotonic clock.
 */
class StreamService : AccessibilityService() {

    private lateinit var prefs: Prefs
    private val handler = Handler(Looper.getMainLooper())

    private var parseScheduled = false
    private var lastParseAt = 0L
    private var lastPostAttemptAt = 0L

    private val parseRunnable = Runnable {
        parseScheduled = false
        parseOnce()
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
        isConnected = false
        Log.i(TAG, "Accessibility service destroyed")
        super.onDestroy()
    }

    // ------------------------------------------------------------------ throttle

    private fun scheduleParse(delayMs: Long) {
        if (!::prefs.isInitialized || !prefs.enabled) return
        if (parseScheduled) return
        val sinceLastParse = SystemClock.elapsedRealtime() - lastParseAt
        if (lastParseAt != 0L && sinceLastParse < PARSE_INTERVAL_MS) return
        parseScheduled = true
        handler.postDelayed(parseRunnable, delayMs)
    }

    // ------------------------------------------------------------------ parsing

    private fun parseOnce() {
        if (!prefs.enabled) return

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
        val texts = collectTexts(root)
        val reading = HealthParser.parse(texts)
        lastParsedSummary = reading.summary()
        Log.i(TAG, "Parsed ${texts.size} text nodes -> ${reading.summary()}")

        if (!reading.hasAny) {
            Log.i(TAG, "No genuine reading on the Health screen; not posting")
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
        if (lastPostAttemptAt != 0L && now - lastPostAttemptAt < POST_INTERVAL_MS) {
            Log.i(
                TAG,
                "POST throttled (${now - lastPostAttemptAt} ms since last attempt; " +
                    "minimum ${POST_INTERVAL_MS} ms)"
            )
            return
        }
        lastPostAttemptAt = now
        send(reading)
    }

    private fun send(reading: HealthParser.Reading) {
        val endpoint = prefs.endpoint
        val token = prefs.token
        val timestamp = utcNowIso()
        val body = buildPayload(reading, timestamp).toString()
        val values = reading.display()

        Thread {
            var result: String
            var connection: HttpURLConnection? = null
            try {
                connection = URL(endpoint).openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.connectTimeout = TIMEOUT_MS
                connection.readTimeout = TIMEOUT_MS
                connection.doOutput = true
                connection.useCaches = false
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("X-KinetiQ-Token", token)

                val bytes = body.toByteArray(Charsets.UTF_8)
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { it.write(bytes) }

                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val responseText = try {
                    stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
                        ?.trim()?.take(400).orEmpty()
                } catch (e: Exception) {
                    ""
                }
                result = if (code in 200..299) {
                    "HTTP $code"
                } else {
                    "HTTP $code: $responseText"
                }
                Log.i(TAG, "POST $endpoint -> $result (${reading.summary()})")
            } catch (e: Exception) {
                result = "error: ${e.javaClass.simpleName}: ${e.message}"
                Log.w(TAG, "POST $endpoint failed: $result")
            } finally {
                try {
                    connection?.disconnect()
                } catch (ignored: Exception) {
                    // nothing useful to do here
                }
            }

            val time = System.currentTimeMillis()
            prefs.recordPost(result, values, time)
            lastResult = result
            lastPostedValues = values
            lastPostTimeMillis = time
        }.apply { isDaemon = true }.start()
    }

    /** Exact contract from post_healthapp.py. */
    private fun buildPayload(reading: HealthParser.Reading, timestamp: String): JSONObject {
        val body = JSONObject()
        body.put("schema_version", 1)
        body.put("source", SOURCE)
        body.put("timestamp", timestamp)
        body.put("heart_rate_bpm", reading.heartRateBpm ?: JSONObject.NULL)
        body.put("spo2_percent", reading.spo2Percent?.toDouble() ?: JSONObject.NULL)
        body.put("stress_score", reading.stressScore ?: JSONObject.NULL)
        body.put(
            "heart_rate_mode",
            if (reading.heartRateBpm != null) "realtime" else "unavailable"
        )
        // Health's SpO2 tile is typically the latest automatic measurement.
        body.put(
            "spo2_mode",
            if (reading.spo2Percent != null) "historical" else "unavailable"
        )
        body.put(
            "stress_mode",
            if (reading.stressScore != null) "realtime" else "unavailable"
        )
        body.put("status", "ok")
        body.put("device_model", DEVICE_MODEL)
        body.put("simulated", false)
        body.put("transport", TRANSPORT)
        return body
    }

    private fun utcNowIso(): String {
        val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        format.timeZone = TimeZone.getTimeZone("UTC")
        return format.format(Date())
    }

    companion object {
        const val TAG = "KinetiQ-Stream"
        const val TARGET_PACKAGE = "com.huawei.health"

        /** Parse at most once per 10 s. */
        const val PARSE_INTERVAL_MS = 10_000L

        /** POST at most once per 15 s. */
        const val POST_INTERVAL_MS = 15_000L

        private const val FIRST_PARSE_DELAY_MS = 600L
        private const val SETTLE_DELAY_MS = 350L
        private const val TIMEOUT_MS = 5_000
        private const val MAX_DEPTH = 48
        private const val MAX_NODES = 2000

        // Fixed honesty fields: never changed at runtime.
        private const val SOURCE = "huawei_band_6_healthapp"
        private const val TRANSPORT = "health_app_ui"
        private const val DEVICE_MODEL = "Huawei Band 6 (via Health UI)"

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
    }
}
