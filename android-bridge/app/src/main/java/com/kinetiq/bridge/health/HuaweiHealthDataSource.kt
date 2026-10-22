package com.kinetiq.bridge.health

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.util.Log
import com.huawei.hms.hihealth.HiHealthStatusCodes
import com.huawei.hms.hihealth.HuaweiHiHealth
import com.huawei.hms.hihealth.data.DataType
import com.huawei.hms.hihealth.data.SamplePoint
import com.huawei.hms.hihealth.data.Scopes
import com.huawei.hms.hihealth.options.ReadOptions
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume

/**
 * Huawei Health Kit access for the KinetiQ bridge.
 *
 * API signatures below are taken from Huawei's official sample
 * (HMS-Core/hms-health-demo-kotlin):
 *  - HuaweiHiHealth.getSettingController / getDataController
 *  - SettingController.requestAuthorizationIntent(scopes, true)
 *  - SettingController.parseHealthKitAuthResultFromIntent(data)
 *  - DataController.read(ReadOptions) / readLatestData(list)
 *  - ReadOptions.Builder().read(type).setTimeRange(s, e, unit).build()
 *  - Scopes.HEALTHKIT_HEARTRATE_READ (same family as the demo's STEP/HEIGHT ones)
 *  - DataType.DT_INSTANTANEOUS_HEART_RATE (listed in Health Kit's
 *    readTodaySummation supported types)
 *
 * VERIFICATION POINTS (must be confirmed in AppGallery Connect / SDK at build
 * time; the build is expected to fail or the scope request to be rejected
 * otherwise — never silently worked around):
 *  [V1] Scope constant for real-time heart rate. Requested below following the
 *       documented STEP_REALTIME naming pattern; confirm the exact
 *       "real-time heart rate" scope string in the console's Health Kit scope
 *       list and fix [V1] if it differs.
 *  [V2] DataType for SpO2 ("com.huawei.instantaneous.spo2" per Huawei docs).
 *       The read method resolves it defensively and reports UNSUPPORTED when
 *       the type/permission is unavailable — SpO2 is queried as the latest
 *       available sample, never assumed continuous (Band 6 measures SpO2
 *       on demand / at rest only).
 *
 * Deliberately NOT used: AutoRecorderController for heart rate — Huawei's own
 * plugin docs state it supports real-time reading only for
 * DT_CONTINUOUS_STEPS_TOTAL. Near-real-time HR here = short-interval polling
 * of the documented read APIs, and heart_rate_mode is derived from the actual
 * sample age so stored data is never presented as live.
 */
class HuaweiHealthDataSource(private val context: Context) {

    companion object {
        private const val TAG = "KinetiQ-Health"
        const val AUTH_REQUEST_CODE = 1001

        /** Data-type string asserted by Huawei docs for instantaneous SpO2. */
        const val SPO2_DATA_TYPE_NAME = "com.huawei.instantaneous.spo2"
        const val HR_DATA_TYPE_NAME = "com.huawei.instantaneous.heart_rate"

        /** A sample is "realtime" only if fresher than this. */
        const val REALTIME_FRESHNESS_MS = 15_000L
    }

    sealed interface AuthState {
        data object Unknown : AuthState
        data object Granted : AuthState
        data class Denied(val errorCode: Int, val message: String) : AuthState
    }

    data class HeartReading(
        val bpm: Int?,
        val sampleTimeMs: Long?,
        /** realtime | historical | unavailable — derived from sample age. */
        val mode: String,
    )

    data class Spo2Reading(
        val percent: Double?,
        val sampleTimeMs: Long?,
        /** realtime | historical | unavailable | unsupported */
        val mode: String,
    )

    // ------------------------------------------------------------------ auth

    /**
     * Builds the Health Kit authorisation intent. Call from an Activity, then
     * forward onActivityResult to [handleAuthResult].
     * Pattern verified from HealthKitAuthActivity in the official demo.
     */
    fun authIntent(): Intent {
        val settingController = HuaweiHiHealth.getSettingController(context)
        val scopes = arrayOf(
            Scopes.HEALTHKIT_HEARTRATE_READ,
            // [V1] real-time heart-rate scope: confirm exact constant against
            // the SDK's Scopes class / console scope list at build time.
            Scopes.HEALTHKIT_HEARTRATE_REALTIME,
            // [V2] SpO2 scope: confirm against the console scope list. If the
            // SDK/approval does not offer it, remove this entry — SpO2 reads
            // will then surface as permission-denied/unsupported, by design.
            Scopes.HEALTHKIT_SPO2_READ,
        )
        // 'true' enables the Huawei Health app authorisation flow (per demo).
        return settingController.requestAuthorizationIntent(scopes, true)
    }

    fun handleAuthResult(data: Intent?): AuthState {
        return try {
            val controller = HuaweiHiHealth.getSettingController(context)
            val result = controller.parseHealthKitAuthResultFromIntent(data)
                ?: return AuthState.Denied(-1, "Empty authorisation result")
            if (result.isSuccess) AuthState.Granted
            else AuthState.Denied(
                result.errorCode,
                HiHealthStatusCodes.getStatusCodeMessage(result.errorCode)
                    ?: "Authorisation failed",
            )
        } catch (e: Exception) {
            Log.w(TAG, "auth result parse failed", e)
            AuthState.Denied(-1, e.message ?: "Authorisation failed")
        }
    }

    // ------------------------------------------------------------------ reads

    /** Latest heart-rate sample (polling path for near-real-time display). */
    suspend fun readLatestHeartRate(): HeartReading {
        return try {
            val controller = HuaweiHiHealth.getDataController(context)
            val map = awaitTask(
                controller.readLatestData(arrayListOf(DataType.DT_INSTANTANEOUS_HEART_RATE))
            )
            val point = map[DataType.DT_INSTANTANEOUS_HEART_RATE]
                ?: return HeartReading(null, null, "unavailable")
            val bpm = extractNumber(point, listOf("bpm", "heart", "rate"))?.toInt()
            if (bpm == null || bpm < 30 || bpm > 220) {
                // Genuine-only rule: out-of-range sensor values are dropped,
                // never forwarded. Visible as unavailable, not zero.
                HeartReading(null, pointEndMs(point), "unavailable")
            } else {
                HeartReading(bpm, pointEndMs(point), modeFor(pointEndMs(point)))
            }
        } catch (e: Exception) {
            Log.w(TAG, "HR read failed: ${safeMsg(e)}")
            HeartReading(null, null, "unavailable")
        }
    }

    /**
     * Latest available SpO2 sample. Single-shot query — SpO2 is NOT assumed
     * continuous. Returns mode=unsupported when the type or permission is
     * unavailable so the UI can say so explicitly.
     */
    suspend fun readLatestSpo2(): Spo2Reading {
        return try {
            val controller = HuaweiHiHealth.getDataController(context)
            // [V2] constant to confirm: DataType.DT_INSTANTANEOUS_SPO2
            // (name "com.huawei.instantaneous.spo2").
            val map = awaitTask(
                controller.readLatestData(arrayListOf(DataType.DT_INSTANTANEOUS_SPO2))
            )
            val point = map[DataType.DT_INSTANTANEOUS_SPO2]
                ?: return Spo2Reading(null, null, "unavailable")
            val value = extractNumber(point, listOf("spo2", "oxygen", "saturation"))
            if (value == null || value < 70.0 || value > 100.0) {
                Spo2Reading(null, pointEndMs(point), "unavailable")
            } else {
                Spo2Reading(value, pointEndMs(point), modeFor(pointEndMs(point)))
            }
        } catch (e: Exception) {
            if (isUnsupportedOrDenied(e)) {
                Log.w(TAG, "SpO2 unsupported/denied: ${safeMsg(e)}")
                Spo2Reading(null, null, "unsupported")
            } else {
                Log.w(TAG, "SpO2 read failed: ${safeMsg(e)}")
                Spo2Reading(null, null, "unavailable")
            }
        }
    }

    /**
     * Windowed heart-rate history (verified ReadOptions pattern from the
     * official DataController demo). Used to backfill / confirm freshness.
     */
    suspend fun readHeartRateWindow(startMs: Long, endMs: Long): List<HeartReading> {
        return try {
            val controller = HuaweiHiHealth.getDataController(context)
            val options = ReadOptions.Builder()
                .read(DataType.DT_INSTANTANEOUS_HEART_RATE)
                .setTimeRange(startMs, endMs, TimeUnit.MILLISECONDS)
                .build()
            val reply = awaitTask(controller.read(options))
            reply.sampleSets.flatMap { it.samplePoints }.mapNotNull { point ->
                val bpm = extractNumber(point, listOf("bpm", "heart", "rate"))?.toInt()
                    ?: return@mapNotNull null
                if (bpm in 30..220) HeartReading(bpm, pointEndMs(point), modeFor(pointEndMs(point)))
                else null
            }
        } catch (e: Exception) {
            Log.w(TAG, "HR window read failed: ${safeMsg(e)}")
            emptyList()
        }
    }

    // ---------------------------------------------------------------- helpers

    private fun pointEndMs(point: SamplePoint): Long =
        try {
            point.getEndTime(TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            System.currentTimeMillis()
        }

    private fun modeFor(sampleMs: Long): String {
        val age = System.currentTimeMillis() - sampleMs
        return if (age in 0..REALTIME_FRESHNESS_MS) "realtime" else "historical"
    }

    /**
     * Extracts a numeric field without depending on exact Field constants:
     * matches (case-insensitively) preferred names first, then any numeric
     * field. Returns null when nothing numeric is present.
     */
    private fun extractNumber(point: SamplePoint, preferred: List<String>): Double? {
        return try {
            val fields = point.dataType.fields
            val ordered = fields.sortedBy { f ->
                val name = f.name.lowercase()
                preferred.indexOfFirst { name.contains(it) }.let { if (it == -1) Int.MAX_VALUE else it }
            }
            for (field in ordered) {
                val num = try {
                    point.getFieldValue(field)?.toString()?.toDoubleOrNull()
                } catch (e: Exception) {
                    null
                }
                if (num != null) return num
            }
            null
        } catch (e: Exception) {
            null
        }
    }

    private fun isUnsupportedOrDenied(e: Exception): Boolean {
        val msg = (e.message ?: "").lowercase()
        return "50011" in msg || "scope" in msg || "permission" in msg ||
            "denied" in msg || "not support" in msg || "unsupported" in msg
    }

    /** Never include tokens/IDs in logs; status codes only. */
    private fun safeMsg(e: Exception): String =
        (e.message ?: e.javaClass.simpleName).take(160)

    private suspend fun <T> awaitTask(task: com.huawei.hmf.tasks.Task<T>): T =
        suspendCancellableCoroutine { cont ->
            task.addOnSuccessListener { cont.resume(it) }
            task.addOnFailureListener { e -> cont.resumeWith(Result.failure(e)) }
        }

    /** Convenience for Activities without coroutines wired up. */
    fun debugAvailability(): String {
        return try {
            val availability = com.huawei.hms.api.HuaweiApiAvailability.getInstance()
            val code = availability.isHuaweiMobileServicesAvailable(context)
            if (code == com.huawei.hms.api.ConnectionResult.SUCCESS) "available"
            else "unavailable (code=$code)"
        } catch (e: Exception) {
            "unknown (${e.javaClass.simpleName})"
        }
    }
}

/** Starts the Health Kit authorisation flow from an Activity. */
fun Activity.requestHealthAuth(requestCode: Int = HuaweiHealthDataSource.AUTH_REQUEST_CODE) {
    val intent = HuaweiHealthDataSource(this).authIntent()
    @Suppress("DEPRECATION")
    startActivityForResult(intent, requestCode)
}
