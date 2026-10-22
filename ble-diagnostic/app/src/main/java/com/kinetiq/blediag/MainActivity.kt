package com.kinetiq.blediag

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.ListView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import com.kinetiq.blediag.ble.BleScanner
import com.kinetiq.blediag.ble.GattClient
import com.kinetiq.blediag.util.DiagLog

/**
 * KinetiQ BLE diagnostic app (Band 6 investigation, Task 002).
 *
 * Flow: request Bluetooth 13 permissions → passive scan (names, addresses,
 * RSSI, advertised service UUIDs) → user explicitly taps the Band 6 row →
 * Connect only on button press → service/characteristic dump → passive
 * notification hex annotation → Disconnect/Cleanup.
 *
 * Privacy: only the USER-SELECTED device is written to the diagnostic log.
 * No automatic unpairing, no band settings changes, no reset calls.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var scanner: BleScanner
    private lateinit var gatt: GattClient
    private lateinit var wearProbe: WearEngineProbe
    private var scanned: List<BleScanner.Row> = emptyList()
    private var shownRows: List<BleScanner.Row> = emptyList()
    private var selected: BleScanner.Row? = null

    private lateinit var status: TextView
    private lateinit var hint: TextView
    private lateinit var devices: ListView
    private lateinit var filterInput: EditText
    private lateinit var logView: TextView
    private lateinit var adapter: ArrayAdapter<String>

    private val onScreen = StringBuilder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        scanner = BleScanner(this)
        gatt = GattClient(this)
        wearProbe = WearEngineProbe(this)

        status = findViewById(R.id.status)
        hint = findViewById(R.id.hint)
        devices = findViewById(R.id.devices)
        filterInput = findViewById(R.id.filterInput)
        logView = findViewById(R.id.logView)
        adapter = ArrayAdapter(this, android.R.layout.simple_list_item_1,
            mutableListOf("no devices yet — Scan first"))
        devices.adapter = adapter
        filterInput.setText("band")

        devices.setOnItemClickListener { _, _, position, _ ->
            if (position < shownRows.size) {
                selected = shownRows[position]
                status.text = "selected: ${selected!!.name} ${selected!!.address}"
                DiagLog.append(this, "user-selected device: ${selected!!.name} ${selected!!.address}")
                hint.text = "Press Connect when ready. The band's single BLE link may be " +
                    "held by the Huawei Health app — if connect fails, that is the likely cause."
            }
        }

        findViewById<Button>(R.id.permissionBtn).setOnClickListener { ensurePermissions() }
        findViewById<Button>(R.id.scanBtn).setOnClickListener { toggleScan() }
        findViewById<Button>(R.id.connectBtn).setOnClickListener { connectSelected() }
        findViewById<Button>(R.id.disconnectBtn).setOnClickListener { disconnect(true) }
        findViewById<Button>(R.id.bondedBtn).setOnClickListener { showBonded() }
        findViewById<Button>(R.id.readBtn).setOnClickListener { gatt.readDiagnostics { onLog(it) } }
        findViewById<Button>(R.id.probeBtn).setOnClickListener { gatt.runProbes { onLog(it) } }
        findViewById<Button>(R.id.wearBtn).setOnClickListener { wearProbe.run { onLog(it) } }

        warn("Unpaired band usually shows as '(unnamed)'. Connect Huawei Health once " +
            "if needed — this app never modifies the band's pairing or settings.")
    }

    // ------------------------------------------------------------ permissions

    private fun ensurePermissions(): Boolean {
        val needed = mutableListOf(
            Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        val missing = needed.filter {
            ActivityCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        return if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 2001)
            false
        } else true
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 2001 && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
            status.text = "Bluetooth permissions granted — Scan now"
        } else {
            status.text = "permissions denied — scanning unavailable"
        }
    }

    // ------------------------------------------------------------- scanning

    private fun toggleScan() {
        if (!ensurePermissions()) return
        if (scanner.isScanning()) {
            scanner.stopScan()
            status.text = "scan stopped"
            findViewById<Button>(R.id.scanBtn).text = "Scan"
            return
        }
        status.text = "scanning…"
        scanner.startScan(this, { rows ->
            scanned = rows
            val f = filterInput.text.toString().trim().lowercase()
            val shown = rows.filter { f.isBlank() || it.name.lowercase().contains(f) }
            shownRows = shown
            adapter.clear()
            if (shown.isEmpty()) adapter.add("(no matches for filter \"$f\" — clear it to see all)")
            shown.forEach { r ->
                adapter.add("%s  %s  %d dBm%s".format(
                    r.name.ifBlank { "(unnamed)" }, r.address, r.rssi,
                    if (r.uuids.isEmpty()) "" else " · ${r.uuids.joinToString(",")}"))
            }
        }, { err ->
            status.text = err
        })
        if (scanner.isScanning()) findViewById<Button>(R.id.scanBtn).text = "Stop scan"
    }

    private fun showBonded() {
        if (!ensurePermissions()) return
        stopScanIfActive()
        shownRows = scanner.bondedRows(this)
        selected = null
        adapter.clear()
        if (shownRows.isEmpty()) {
            adapter.add("(no bonded LE devices — connect Huawei Health once to bond)")
            status.text = "no bonded LE devices"
        } else {
            shownRows.forEach { r ->
                adapter.add("%s  %s  · bonded (no RSSI)".format(r.name, r.address))
            }
            status.text = "bonded devices — tap the band row, then Connect"
            DiagLog.append(this, "showed bonded device list (${shownRows.size} item(s))")
        }
    }

    private fun stopScanIfActive() {
        if (scanner.isScanning()) {
            scanner.stopScan()
            findViewById<Button>(R.id.scanBtn).text = "Scan"
        }
    }

    // ----------------------------------------------------------- connection

    private fun connectSelected() {
        val row = selected
        if (row == null) {
            status.text = "tap a device row first (explicit identification required)"
            return
        }
        if (!ensurePermissions()) return
        status.text = "connecting to ${row.name}…"
        DiagLog.append(this, "connect(attempt): ${row.name} ${row.address}")
        gatt.connect(row.device,
            { _, note -> if (note.isNotEmpty()) { status.text = note; DiagLog.append(this, "state: $note") } },
            { line -> onLog(line) })
    }

    private fun disconnect(userPressed: Boolean) {
        gatt.disconnect()
        if (userPressed) {
            status.text = "disconnected"
            onLog("user requested disconnect — GATT closed, nothing modified on the band")
        }
    }

    override fun onDestroy() {
        scanner.stopScan()
        disconnect(false)   // lifecycle cleanup: never leaves a dangling GATT
        if (this::wearProbe.isInitialized) wearProbe.stop { DiagLog.append(this, it) }
        super.onDestroy()
    }

    // ------------------------------------------------------------------- ui

    private fun onLog(line: String) {
        DiagLog.append(this, line)
        runOnUiThread {
            onScreen.append(line).append('\n')
            if (onScreen.length > 14000) {
                onScreen.delete(0, onScreen.length - 10000)
            }
            logView.text = onScreen.toString()
        }
    }

    private fun warn(line: String) {
        DiagLog.append(this, line)
        runOnUiThread { hint.text = line }
    }
}
