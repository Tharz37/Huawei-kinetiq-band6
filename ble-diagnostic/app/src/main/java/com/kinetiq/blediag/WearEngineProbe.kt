package com.kinetiq.blediag

import android.app.Activity
import com.huawei.wearengine.HiWear
import com.huawei.wearengine.auth.AuthCallback
import com.huawei.wearengine.auth.Permission
import com.huawei.wearengine.device.Device
import com.huawei.wearengine.sensor.DataResult
import com.huawei.wearengine.sensor.Sensor
import com.huawei.wearengine.sensor.SensorReadCallback
import com.huawei.wearengine.sensor.SensorStopCallback
import java.util.Arrays

/**
 * Wear Engine probe — the OFFICIAL Huawei channel that runs THROUGH the
 * Huawei Health app (no HMS Core involved): the SDK is a plain client that
 * binds a service hosted by com.huawei.health.
 *
 * Dissection evidence: com.huawei.healthcloud.health.provider/wear_device_state
 * answers permission-free on this phone, proving the Health-hosted wearEngine
 * surface is live.
 *
 * Sequence (each step logged verbatim):
 *   1. getBondedDevices()      — what Health exposes as wearables
 *   2. requestPermission(...)  — Health-hosted consent dialog (user taps)
 *   3. getSensorList(band)     — which sensors Band 6 exposes to third parties
 *   4. asyncRead(band, sensor) — live readings, auto-stops after MAX_SAMPLES
 *
 * Rules honored: nothing on the band is modified; authorization is the
 * user's own tap; readings are only logged locally, never sent anywhere.
 */
class WearEngineProbe(private val activity: Activity) {

    private companion object {
        const val MAX_SAMPLES = 12
    }

    private var samples = 0
    private var subscribedDevice: Device? = null
    private var subscribedSensor: Sensor? = null

    /** Entry point: list devices, then chain auth → sensors → readings. */
    fun run(log: (String) -> Unit) {
        log("wearEngine: getBondedDevices() via the Health app…")
        HiWear.getDeviceClient(activity).bondedDevices
            .addOnSuccessListener { list ->
                if (list.isNullOrEmpty()) {
                    log("wearEngine: no bonded devices returned — is the band connected in Health?")
                    return@addOnSuccessListener
                }
                list.forEach { d ->
                    log("wearEngine device: name=${d.name} model=${d.model} " +
                        "connected=${d.isConnected} productType=${d.productType} uuid=${d.uuid}")
                }
                val band = list.firstOrNull { it.isConnected } ?: list[0]
                log("wearEngine: selected '${band.name}' → requesting authorization " +
                    "(a Health consent dialog will appear — please allow it)")
                requestAuth(band, log)
            }
            .addOnFailureListener { e ->
                log("wearEngine FAIL getBondedDevices: ${describe(e)}")
            }
    }

    /** Full diagnosis: wearEngine code (if present) + first stack frames. */
    private fun describe(e: Exception): String {
        val code = (e as? com.huawei.wearengine.WearEngineException)?.errorCode
        val frames = e.stackTrace.take(5).joinToString(" | ") { f ->
            "${f.className.substringAfterLast('.')}.${f.methodName}:${f.lineNumber}"
        }
        return "code=$code $e  at  $frames"
    }

    private fun requestAuth(band: Device, log: (String) -> Unit) {
        HiWear.getAuthClient(activity)
            .requestPermission(object : AuthCallback {
                override fun onOk(permissions: Array<Permission>?) {
                    log("wearEngine: authorization GRANTED for " +
                        (permissions?.joinToString { p -> p.name } ?: "(null)"))
                    listSensors(band, log)
                }

                override fun onCancel() {
                    log("wearEngine: authorization CANCELLED by user — stopping here")
                }
            }, Permission.DEVICE_MANAGER, Permission.SENSOR)
            .addOnFailureListener { e ->
                log("wearEngine FAIL requestPermission: ${describe(e)}")
            }
    }

    private fun listSensors(band: Device, log: (String) -> Unit) {
        log("wearEngine: getSensorList(${band.name})…")
        HiWear.getSensorClient(activity).getSensorList(band)
            .addOnSuccessListener { sensors ->
                if (sensors.isNullOrEmpty()) {
                    log("wearEngine: sensor list EMPTY — Band 6 exposes none to third-party apps")
                    return@addOnSuccessListener
                }
                sensors.forEach { s ->
                    log("wearEngine sensor: id=${s.id} type=${s.type} name=${s.name} " +
                        "resolution=${s.resolution} utc=${s.isSupportUtc} " +
                        "freqs=${s.supportFrequencyList?.joinToString()}")
                }
                // Prefer the computed heart-rate sensor, then PPG, then ECG.
                val pick = sensors.firstOrNull { it.type == Sensor.TYPE_HR }
                    ?: sensors.firstOrNull { it.type == Sensor.TYPE_PPG }
                    ?: sensors.firstOrNull { it.type == Sensor.TYPE_ECG }
                    ?: sensors[0]
                log("wearEngine: subscribing to '${pick.name}' (type=${pick.type}) " +
                    "for up to $MAX_SAMPLES readings…")
                subscribe(band, pick, log)
            }
            .addOnFailureListener { e ->
                log("wearEngine FAIL getSensorList: ${describe(e)}")
            }
    }

    private fun subscribe(band: Device, sensor: Sensor, log: (String) -> Unit) {
        samples = 0
        subscribedDevice = band
        subscribedSensor = sensor
        val callback = SensorReadCallback { sensorTypeId, data ->
            onReading(sensorTypeId, data, log)
        }
        HiWear.getSensorClient(activity).asyncRead(band, sensor, callback)
            .addOnSuccessListener {
                log("wearEngine: asyncRead registered — waiting for readings (band must be worn)")
            }
            .addOnFailureListener { e ->
                log("wearEngine FAIL asyncRead: ${describe(e)}")
            }
    }

    private fun onReading(sensorTypeId: Int, data: DataResult?, log: (String) -> Unit) {
        if (data == null) return
        samples++
        log("wearEngine READING #$samples sensorTypeId=$sensorTypeId " +
            "channel=${data.channel} utc=${data.utc} " +
            "values=${Arrays.toString(data.asFloats())} " +
            "sub-results=${data.asList()?.size ?: 0}")
        if (samples >= MAX_SAMPLES) stop(log)
    }

    /** Stop the subscription (called automatically at MAX_SAMPLES). */
    fun stop(log: (String) -> Unit) {
        val d = subscribedDevice ?: return
        val s = subscribedSensor ?: return
        subscribedDevice = null
        subscribedSensor = null
        HiWear.getSensorClient(activity).stopAsyncRead(d, s) { resultCode ->
            log("wearEngine: asyncRead stopped (resultCode=$resultCode) after $samples samples")
        }
    }
}
