package com.kinetiq.blediag.ble

import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.Context
import com.kinetiq.blediag.util.DiagLog
import java.util.UUID

/**
 * Minimal GATT client for diagnostics. Connects only when the user pressed
 * Connect; discovers services; enables notifications passively; reads the
 * documented standard characteristics (queued one-per-callback so reads
 * never collide); can send the DOCUMENTED plaintext LPv2 probes
 * (1/2 SupportedServices, 1/3 SupportedCommands) via the FE01 write chat.
 *
 * Standard BLE SIG UUIDs used for the documented reads:
 *  2A19 Battery, 2A24 Model, 2A26 Firmware, 2A29 Manufacturer, 2A37 HR meas,
 *  2A38 Body sensor location. (zyv/huawei-lpv2 documented the band's 2A37 as
 *  "Not permitted" — we verify on OUR band and report honestly either way.)
 *
 * Safety: nothing here alters band state — probes ask for capability
 * summaries only; auth (1/19), bond (1/14) and settings commands are never
 * sent. Notification bytes are annotated as raw hex, never presented as
 * health readings.
 */
class GattClient(context: Context) {

    companion object {
        // Standard Health / device-info characteristics (Bluetooth SIG).
        val BATTERY = UUID.fromString("00002a19-0000-1000-8000-00805f9b34fb")
        val MODEL = UUID.fromString("00002a24-0000-1000-8000-00805f9b34fb")
        val FIRMWARE = UUID.fromString("00002a26-0000-1000-8000-00805f9b34fb")
        val MANUFACTURER = UUID.fromString("00002a29-0000-1000-8000-00805f9b34fb")
        val HR_MEASUREMENT = UUID.fromString("00002a37-0000-1000-8000-00805f9b34fb")
        val BODY_LOCATION = UUID.fromString("00002a38-0000-1000-8000-00805f9b34fb")
        val CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
        val HEART_RATE_SERVICE = UUID.fromString("0000180d-0000-1000-8000-00805f9b34fb")

        // Huawei vendor LPv2 channel (service 0xFE86: FE01 write / FE02 notify).
        val LPV2_SERVICE = UUID.fromString("0000fe86-0000-1000-8000-00805f9b34fb")
        val LPV2_WRITE = UUID.fromString("0000fe01-0000-1000-8000-00805f9b34fb")

        // DeviceConfig probe command IDs (docs/RESEARCH_LPV2.md):
        // service 1 with commands SupportedServices(2), SupportedCommands(3).
        const val SVC_DEVICECONFIG = 1
        const val CMD_SUPPORTED_SERVICES = 2
        const val CMD_SUPPORTED_COMMANDS = 3
    }

    private val gattManager =
        context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private var gatt: BluetoothGatt? = null
    private var sink: (String) -> Unit = {}
    private val readQueue = ArrayDeque<BluetoothGattCharacteristic>()
    private var nextAfterWrite: (() -> Unit)? = null

    val isConnected: Boolean
        get() = gatt != null

    /**
     * Connect on explicit user action. onState(connected, note) reports the
     * ATT link state; note can flag the "taken by Huawei Health" case.
     */
    fun connect(
        device: BluetoothDevice,
        onState: (connected: Boolean, note: String) -> Unit,
        onLog: (String) -> Unit,
    ) {
        disconnect()
        sink = onLog
        try {
            gatt = device.connectGatt(
                null, false/*autoConnect=false: user-driven only*/,
                object : BluetoothGattCallback() {

                    override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
                        when (newState) {
                            BluetoothProfile.STATE_CONNECTED -> {
                                sink("connected (status=$status)")
                                g.discoverServices()
                                onState(true, "")
                            }
                            BluetoothProfile.STATE_DISCONNECTED -> {
                                sink("disconnected (status=$status)")
                                if (status != 0) {
                                    // 133 etc. — commonly the band's single ATT link is held
                                    // by Huawei Health, or pairing/auth is required.
                                    onState(false, "connect failed (status=$status). " +
                                        "The band may be busy with the Huawei Health app. " +
                                        "Disconnecting Health there is a user decision.")
                                } else {
                                    onState(false, "")
                                }
                            }
                        }
                    }

                    override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
                        if (status != 0) { sink("service discovery failed ($status)"); return }
                        val sb = StringBuilder()
                        for (svc in g.services) {
                            sb.append("\nService ${svc.uuid}")
                            if (svc.instanceId != 0) sb.append("(inst ${svc.instanceId})")
                            for (c in svc.characteristics) {
                                val props = propertyNames(c.properties)
                                sb.append("\n  char ${c.uuid} [${props}]")
                                if (c.descriptors.isNotEmpty()) sb.append(" (CCCD)")
                            }
                        }
                        sink("services discovered:$sb")
                        enablePassiveNotifications(g)
                    }

                    override fun onCharacteristicChanged(
                        g: BluetoothGatt,
                        c: BluetoothGattCharacteristic,
                        value: ByteArray,
                    ) {
                        // PASSIVE observation: annotate the wire bytes only.
                        // No value here is ever reported as a health reading.
                        sink("notify ${c.uuid}: ${Lpv2.hex(value)}")
                        sink("  └ " + Lpv2.classify(value))
                    }

                    override fun onCharacteristicRead(
                        g: BluetoothGatt,
                        c: BluetoothGattCharacteristic,
                        value: ByteArray,
                        status: Int,
                    ) {
                        sink(if (status == 0) "read ${c.uuid}: ${Lpv2.hex(value)}"
                             else "read ${c.uuid} failed ($status)")
                        drainReads(g)
                    }

                    override fun onCharacteristicWrite(
                        g: BluetoothGatt,
                        c: BluetoothGattCharacteristic,
                        status: Int,
                    ) {
                        sink("write ${c.uuid} done (status=$status)")
                        nextAfterWrite?.let { next -> next() }
                        nextAfterWrite = null
                    }
                })
        } catch (e: SecurityException) {
            sink("connect blocked: missing BLUETOOTH_CONNECT")
            gatt = null
        }
    }

    private fun propertyNames(p: Int): String {
        val names = mutableListOf<String>()
        if (p and BluetoothGattCharacteristic.PROPERTY_READ != 0) names.add("read")
        if (p and BluetoothGattCharacteristic.PROPERTY_WRITE != 0) names.add("write")
        if (p and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0) names.add("notify")
        if (p and BluetoothGattCharacteristic.PROPERTY_INDICATE != 0) names.add("indicate")
        return names.joinToString(",")
    }

    /** Subscribe to notifications on non-HR (custom) characteristics. */
    private fun enablePassiveNotifications(g: BluetoothGatt) {
        var enabled = 0
        for (svc in g.services) {
            for (c in svc.characteristics) {
                val notify = c.properties and (
                    BluetoothGattCharacteristic.PROPERTY_NOTIFY or
                    BluetoothGattCharacteristic.PROPERTY_INDICATE) != 0
                if (!notify) continue
                if (svc.uuid == HEART_RATE_SERVICE) {
                    // Do NOT enable 2A37 notifications: any value would be
                    // unauthenticated vendor data, and the task forbids
                    // presenting such bytes as readings.
                    sink("HR service present; 2A37 not subscribed (verification pending)")
                    continue
                }
                try {
                    g.setCharacteristicNotification(c, true)
                    c.getDescriptor(CCCD)?.let { d ->
                        if (c.properties and BluetoothGattCharacteristic.PROPERTY_INDICATE != 0) {
                            d.value = BluetoothGattDescriptor.ENABLE_INDICATION_VALUE
                        } else {
                            d.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                        }
                        g.writeDescriptor(d)
                        enabled++
                    }
                } catch (e: SecurityException) {
                    sink("subscribe failed on ${c.uuid}")
                }
            }
        }
        sink("subscribed to $enabled non-SIG notification characteristic(s)")
        // Standard-tile reads follow after the CCCD writes have settled
        // (reads issued too early return "not triggerable" — GATT busy).
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            gatt?.let { queueStandardReads(it) }
        }, 1500)
    }

    /** Diagnostic reads of documented standard characteristics. */
    fun readDiagnostics(onLog: (String) -> Unit) {
        val g = gatt ?: run { onLog("not connected"); return }
        sink = onLog
        queueStandardReads(g)
    }

    private fun queueStandardReads(g: BluetoothGatt) {
        readQueue.clear()
        for (uuid in listOf(BATTERY, MODEL, FIRMWARE, MANUFACTURER, BODY_LOCATION, HR_MEASUREMENT)) {
            val c = findChar(g, uuid)
            if (c == null) sink("read $uuid: not present") else readQueue.add(c)
        }
        drainReads(g)
    }

    /** One read at a time — GATT refuses concurrent operations. */
    private fun drainReads(g: BluetoothGatt) {
        val c = readQueue.removeFirstOrNull() ?: return
        try {
            if (!g.readCharacteristic(c)) {
                sink("read ${c.uuid}: not triggerable")
                drainReads(g)
            }
        } catch (e: SecurityException) {
            sink("read ${c.uuid}: permission missing")
            drainReads(g)
        }
    }

    /**
     * DOCUMENTED plaintext probes over FE01: 1/2 SupportedServices and
     * 1/3 SupportedCommands. Never sends auth (1/19), bond (1/14) or any
     * settings command. Responses (encrypted or plain) arrive on FE02 and
     * are logged as annotated bytes only.
     */
    fun runProbes(onLog: (String) -> Unit) {
        val g = gatt ?: run { onLog("not connected"); return }
        val c = findChar(g, LPV2_SERVICE, LPV2_WRITE)
        if (c == null) {
            onLog("FE01 not present — no LPv2 write channel on this connection")
            return
        }
        sink = onLog
        val ids = listOf(CMD_SUPPORTED_SERVICES, CMD_SUPPORTED_COMMANDS)
        fun send(i: Int) {
            if (i >= ids.size) { sink("lpv2 probes done"); return }
            val frame = Lpv2.frame(SVC_DEVICECONFIG, ids[i])
            sink("lpv2 probe 1/${ids[i]}: ${Lpv2.hex(frame)}")
            try {
                c.setValue(frame)          // legacy write path, valid on API 33
                if (g.writeCharacteristic(c)) {
                    nextAfterWrite = { send(i + 1) }
                } else {
                    sink("lpv2 probe 1/${ids[i]}: not triggerable")
                    send(i + 1)
                }
            } catch (e: SecurityException) {
                sink("lpv2 probe write blocked (${e.javaClass.simpleName})")
                send(i + 1)
            }
        }
        send(0)
    }

    private fun findChar(g: BluetoothGatt, service: UUID, char: UUID): BluetoothGattCharacteristic? {
        for (svc in g.services) if (svc.uuid == service)
            for (c in svc.characteristics) if (c.uuid == char) return c
        return null
    }

    private fun findChar(g: BluetoothGatt, uuid: UUID): BluetoothGattCharacteristic? {
        for (svc in g.services) for (c in svc.characteristics) if (c.uuid == uuid) return c
        return null
    }

    /** Safe disconnect + resource cleanup. Never unpairs or resets anything. */
    fun disconnect() {
        try {
            nextAfterWrite = null
            readQueue.clear()
            gatt?.disconnect()          // triggers callback cleanup
            gatt?.close()               // releases resources
        } catch (e: Exception) { /* already gone */ }
        gatt = null
    }
}
