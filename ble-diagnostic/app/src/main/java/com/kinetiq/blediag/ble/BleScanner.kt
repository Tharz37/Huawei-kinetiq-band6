package com.kinetiq.blediag.ble

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat

/**
 * Passive BLE discovery. Results go to the UI via callbacks on the main
 * thread; only devices the user explicitly selects are ever written to log.
 *
 * Implementation notes (learned on-device, Nokia C22 / Android 13):
 *  - stopScan must be given the SAME callback instance used at registration
 *    (the earlier version created a fresh one and the stop became a silent
 *    no-op — visible as started=2/stopped=0 in `dumpsys bluetooth_manager`).
 *  - An explicit ScanSettings (LOW_LATENCY, reportDelay=0) is used so the
 *    diagnostics see advertisements immediately instead of batched
 *    opportunistically.
 */
class BleScanner(context: Context) {

    data class Row(
        val name: String,
        val address: String,
        val rssi: Int,
        val uuids: List<String>,
        val device: BluetoothDevice,
    )

    private val adapter: BluetoothAdapter? =
        (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter

    private var scanning: Boolean = false
    private var activeCallback: ScanCallback? = null
    private var resultCount = 0

    fun hasPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_CONNECT) ==
            PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.BLUETOOTH_SCAN) ==
            PackageManager.PERMISSION_GRANTED

    fun isEnabled(): Boolean = adapter?.isEnabled == true
    fun isScanning(): Boolean = scanning

    fun startScan(
        context: Context,
        onResults: (List<Row>) -> Unit,
        onError: (String) -> Unit,
    ): Boolean {
        val a = adapter ?: run { onError("No Bluetooth adapter"); return false }
        if (!a.isEnabled) { onError("Bluetooth is off"); return false }
        if (!hasPermission(context)) { onError("Missing Bluetooth permissions"); return false }
        stopScan()
        val seen = LinkedHashMap<String, Row>()
        val callback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                resultCount++
                val name = try {
                    result.device.name ?: "(unnamed)"
                } catch (e: SecurityException) { "(no permission)" }
                val uuids = result.scanRecord?.serviceUuids
                    ?.map { it.toString() } ?: emptyList()
                seen[result.device.address] =
                    Row(name, result.device.address, result.rssi, uuids, result.device)
                // First result + every 25th is logged for diagnostics.
                if (resultCount == 1 || resultCount % 25 == 0) {
                    com.kinetiq.blediag.util.DiagLog.append(
                        context, "scan result #$resultCount: $name ${result.device.address} " +
                        "${result.rssi} dBm")
                }
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    onResults(seen.values.sortedByDescending { it.rssi })
                }
            }

            override fun onScanFailed(errorCode: Int) {
                scanning = false
                activeCallback = null
                onError("Scan failed (code $errorCode)")
            }
        }
        return try {
            val scanner = a.bluetoothLeScanner ?: run {
                onError("LE scanner unavailable")
                return false
            }
            val settings = ScanSettings.Builder()
                .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
                .setReportDelay(0)
                .build()
            scanner.startScan(null /* no filter: diagnostics first */, settings, callback)
            activeCallback = callback
            scanning = true
            resultCount = 0
            true
        } catch (e: SecurityException) {
            onError("Scan blocked: missing permission")
            false
        } catch (e: Exception) {
            onError("Scan start failed: ${e.javaClass.simpleName}")
            false
        }
    }

    fun stopScan() {
        val cb = activeCallback ?: return
        try {
            adapter?.bluetoothLeScanner?.stopScan(cb)
        } catch (e: Exception) { /* scanner state already gone */ }
        activeCallback = null
        scanning = false
    }

    /**
     * Bonded LE devices from the system bond list (rssi = -1000 marker).
     *
     * Needed when the band is ALREADY connected to this phone by another app
     * (e.g. Huawei Health): a connected peripheral stops general advertising,
     * so a scan can never discover it — but one ACL link is shared by several
     * GATT clients, so connecting by the bonded address can attach without
     * touching the other app. Still 100% user-tap driven.
     */
    fun bondedRows(context: Context): List<Row> {
        val a = adapter ?: return emptyList()
        if (!hasPermission(context) || !a.isEnabled) return emptyList()
        return try {
            a.bondedDevices
                .filter {
                    it.type == BluetoothDevice.DEVICE_TYPE_LE ||
                        it.type == BluetoothDevice.DEVICE_TYPE_DUAL
                }
                .map { d ->
                    val name = try {
                        d.name ?: "(unnamed)"
                    } catch (e: SecurityException) { "(no permission)" }
                    Row(name, d.address, -1000, emptyList(), d)
                }
                .sortedBy { it.name }
        } catch (e: SecurityException) {
            emptyList()
        }
    }
}
