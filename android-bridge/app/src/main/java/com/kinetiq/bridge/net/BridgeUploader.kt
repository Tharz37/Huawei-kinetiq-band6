package com.kinetiq.bridge.net

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant

/**
 * Uploads genuine readings to the laptop receiver (backend/kinetiq_receiver.py).
 *
 * Payload matches laptop schema v1 exactly. Missing readings are sent as null
 * (never zero/invented). The shared token travels in the Authorization header
 * and is NEVER written to logcat.
 */
class BridgeUploader(context: Context) {

    companion object {
        private const val TAG = "KinetiQ-Upload"
        private const val PREFS = "kinetiq_bridge"
        private const val KEY_URL = "laptop_url"   // e.g. http://192.168.1.20:8000
        private const val KEY_TOKEN = "auth_token"
        private const val MAX_ATTEMPTS = 3
    }

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    data class Reading(
        val heartRateBpm: Int?,
        val spo2Percent: Double?,
        val heartRateMode: String,
        val spo2Mode: String,
        /** ok | permission_denied | unsupported | error (phone-side state) */
        val phoneStatus: String = "ok",
        val sampleTime: Instant = Instant.now(),
        val deviceModel: String = "Huawei Band 6",
    )

    sealed interface Result {
        data object Sent : Result
        data class Failed(val reason: String) : Result
        data object NotConfigured : Result
    }

    fun saveConfig(laptopUrl: String, token: String) {
        prefs.edit()
            .putString(KEY_URL, laptopUrl.trim().trimEnd('/'))
            .putString(KEY_TOKEN, token.trim())
            .apply()
    }

    fun currentUrl(): String = prefs.getString(KEY_URL, "") ?: ""
    fun isConfigured(): Boolean =
        currentUrl().isNotBlank() && !getToken().isBlank()

    private fun getToken(): String = prefs.getString(KEY_TOKEN, "") ?: ""

    fun buildPayload(r: Reading): JSONObject = JSONObject()
        .put("schema_version", 1)
        .put("source", "huawei_band_6")
        .put("timestamp", r.sampleTime.toString())
        .put("heart_rate_bpm", r.heartRateBpm ?: JSONObject.NULL)
        .put("spo2_percent", r.spo2Percent ?: JSONObject.NULL)
        .put("heart_rate_mode", r.heartRateMode)
        .put("spo2_mode", r.spo2Mode)
        .put("status", r.phoneStatus)
        .put("device_model", r.deviceModel)
        .put("simulated", false)

    suspend fun upload(r: Reading): Result = withContext(Dispatchers.IO) {
        val base = currentUrl()
        val token = getToken()
        if (base.isBlank() || token.isBlank()) return@withContext Result.NotConfigured
        val body = buildPayload(r).toString().toByteArray(Charsets.UTF_8)
        var lastError = "unknown"
        repeat(MAX_ATTEMPTS) { attempt ->
            try {
                val conn = (URL("$base/api/wearable").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 8000
                    readTimeout = 8000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    // Header only; never log the token value.
                    setRequestProperty("Authorization", "Bearer $token")
                    setFixedLengthStreamingMode(body.size)
                }
                conn.outputStream.use { it.write(body) }
                val code = conn.responseCode
                conn.inputStream.use { it.readBytes() }
                if (code in 200..299) return@withContext Result.Sent
                lastError = "laptop rejected upload (HTTP $code)"
                Log.w(TAG, "upload attempt ${attempt + 1} failed: HTTP $code")
            } catch (e: Exception) {
                lastError = e.javaClass.simpleName + ": network unreachable?"
                Log.w(TAG, "upload attempt ${attempt + 1} failed: ${e.javaClass.simpleName}")
            }
            if (attempt < MAX_ATTEMPTS - 1) delay(1500L * (attempt + 1))
        }
        Result.Failed("$lastError after $MAX_ATTEMPTS attempts")
    }
}
