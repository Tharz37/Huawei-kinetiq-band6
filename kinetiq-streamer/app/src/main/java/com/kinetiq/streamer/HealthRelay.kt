package com.kinetiq.streamer

import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Single wire-contract implementation shared by the accessibility watchdog and
 * the notification listener.
 *
 * Why a shared gate: Accessibility events, the 4-second watchdog and Huawei
 * Health's status-bar notification can observe the same values within a few
 * seconds. Without a process-wide gate the receiver would get duplicate rows
 * (and the CSV would grow four times faster) even though only one physical
 * reading existed. The gate allows an immediate POST when the reading changes,
 * then at most one heartbeat per [SAME_READING_HEARTBEAT_MS] for an unchanged
 * reading.
 *
 * Honesty rules remain with the callers: only [HealthParser.Reading] values are
 * serialised, absent metrics stay JSON null, and a vitals-free packet is
 * refused rather than sent.
 */
object HealthRelay {

    const val SOURCE = "huawei_band_6_healthapp"
    const val TRANSPORT = "health_app_ui"
    const val DEVICE_MODEL = "Huawei Band 6 (via Health UI)"

    /** Minimum spacing between HTTP POST attempts from either service. */
    const val MIN_POST_INTERVAL_MS = 5_000L

    /** Unchanged values are re-posted only this often, as a liveness heartbeat. */
    const val SAME_READING_HEARTBEAT_MS = 15_000L

    /**
     * If the accessibility service parsed Health this recently, a notification
     * is a duplicate of the same on-screen reading and is skipped.
     */
    const val FOREGROUND_GRACE_MS = 5_000L

    private const val TIMEOUT_MS = 5_000
    private const val MAX_RESPONSE_CHARS = 400

    private var lastAttemptAt = 0L
    private var lastSignature: String? = null
    private var lastSignatureAt = 0L

    /**
     * Process-wide POST gate. Uses the monotonic clock so wall-clock changes
     * cannot create bursts or suppress readings indefinitely.
     */
    @Synchronized
    fun tryAcquire(signature: String, nowElapsed: Long): Boolean {
        if (lastAttemptAt != 0L && nowElapsed - lastAttemptAt < MIN_POST_INTERVAL_MS) {
            return false
        }
        if (signature == lastSignature && lastSignatureAt != 0L &&
            nowElapsed - lastSignatureAt < SAME_READING_HEARTBEAT_MS
        ) {
            return false
        }
        lastAttemptAt = nowElapsed
        lastSignature = signature
        lastSignatureAt = nowElapsed
        return true
    }

    /** Exact contract from post_healthapp.py / the receiver's schema v1. */
    fun buildPayload(
        reading: HealthParser.Reading,
        timestamp: String = utcNowIso()
    ): JSONObject {
        val body = JSONObject()
        body.put("schema_version", 1)
        body.put("source", SOURCE)
        body.put("timestamp", timestamp)
        body.put("heart_rate_bpm", reading.heartRateBpm ?: JSONObject.NULL)
        body.put("spo2_percent", reading.spo2Percent?.toDouble() ?: JSONObject.NULL)
        body.put("stress_score", reading.stressScore ?: JSONObject.NULL)
        body.put("steps", reading.steps ?: JSONObject.NULL)
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

    /**
     * Fire-and-forget POST on a daemon thread. [onComplete] runs on that
     * background thread and receives the same result/value/time fields the
     * existing UI and SharedPreferences contract expects.
     */
    fun post(
        endpoint: String,
        token: String,
        reading: HealthParser.Reading,
        tag: String,
        onComplete: (result: String, values: String, timeMillis: Long) -> Unit
    ) {
        if (!reading.hasVitals) {
            Log.w(tag, "Refusing vitals-free payload: ${reading.summary()}")
            return
        }

        val body = buildPayload(reading).toString()
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
                        ?.trim()?.take(MAX_RESPONSE_CHARS).orEmpty()
                } catch (e: Exception) {
                    ""
                }
                result = if (code in 200..299) {
                    "HTTP $code"
                } else {
                    "HTTP $code: $responseText"
                }
                Log.i(tag, "POST $endpoint -> $result (${reading.summary()})")
            } catch (e: Exception) {
                result = "error: ${e.javaClass.simpleName}: ${e.message}"
                Log.w(tag, "POST $endpoint failed: $result")
            } finally {
                try {
                    connection?.disconnect()
                } catch (ignored: Exception) {
                    // nothing useful to do here
                }
            }

            onComplete(result, values, System.currentTimeMillis())
        }.apply { isDaemon = true }.start()
    }

    fun utcNowIso(): String {
        val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        format.timeZone = TimeZone.getTimeZone("UTC")
        return format.format(Date())
    }
}
