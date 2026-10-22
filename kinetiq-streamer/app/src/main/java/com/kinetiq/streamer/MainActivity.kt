package com.kinetiq.streamer

import android.content.ComponentName
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Demo control panel: receiver configuration, accessibility-service status,
 * start/stop switch and the last POST outcome.
 *
 * The connection test is deliberately GET-only; it never sends health data.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs

    private lateinit var endpointInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var saveButton: Button
    private lateinit var testButton: Button
    private lateinit var testResult: TextView
    private lateinit var accessibilityStatus: TextView
    private lateinit var openAccessibilityButton: Button
    private lateinit var openHealthButton: Button
    private lateinit var streamingStatus: TextView
    private lateinit var serviceToggle: Button
    private lateinit var lastValues: TextView
    private lateinit var lastTime: TextView
    private lateinit var lastResult: TextView

    private val uiHandler = Handler(Looper.getMainLooper())
    private val refreshTask = object : Runnable {
        override fun run() {
            refreshStatus()
            uiHandler.postDelayed(this, REFRESH_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = Prefs(this)
        endpointInput = findViewById(R.id.endpointInput)
        tokenInput = findViewById(R.id.tokenInput)
        saveButton = findViewById(R.id.saveButton)
        testButton = findViewById(R.id.testButton)
        testResult = findViewById(R.id.testResult)
        accessibilityStatus = findViewById(R.id.accessibilityStatus)
        openAccessibilityButton = findViewById(R.id.openAccessibilityButton)
        openHealthButton = findViewById(R.id.openHealthButton)
        streamingStatus = findViewById(R.id.streamingStatus)
        serviceToggle = findViewById(R.id.serviceToggle)
        lastValues = findViewById(R.id.lastValues)
        lastTime = findViewById(R.id.lastTime)
        lastResult = findViewById(R.id.lastResult)

        endpointInput.setText(prefs.endpoint)
        tokenInput.setText(prefs.token)
        testResult.text = getString(R.string.not_tested)

        saveButton.setOnClickListener { saveConfig() }
        testButton.setOnClickListener { runConnectionTest(endpointInput.text.toString()) }
        openAccessibilityButton.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        openHealthButton.setOnClickListener { openHuaweiHealth() }
        serviceToggle.setOnClickListener { toggleStreaming() }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
        uiHandler.postDelayed(refreshTask, REFRESH_MS)
    }

    override fun onPause() {
        super.onPause()
        uiHandler.removeCallbacks(refreshTask)
    }

    // ------------------------------------------------------------------ config

    private fun saveConfig() {
        val endpoint = endpointInput.text.toString().trim().trimEnd('/')
        if (!endpoint.startsWith("http://") && !endpoint.startsWith("https://")) {
            Toast.makeText(
                this,
                "Endpoint must start with http:// or https://",
                Toast.LENGTH_LONG
            ).show()
            return
        }
        prefs.endpoint = endpoint
        prefs.token = tokenInput.text.toString().trim()
        endpointInput.setText(prefs.endpoint)
        Toast.makeText(this, "Saved.", Toast.LENGTH_SHORT).show()
        if (prefs.token.isBlank()) {
            Toast.makeText(
                this,
                "Token is empty - the receiver will reject POSTs.",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    private fun toggleStreaming() {
        prefs.enabled = !prefs.enabled
        refreshStatus()
        if (prefs.enabled) {
            if (!isAccessibilityServiceEnabled()) {
                Toast.makeText(
                    this,
                    "Streaming is ON, but the accessibility service is still disabled.",
                    Toast.LENGTH_LONG
                ).show()
            } else {
                Toast.makeText(
                    this,
                    "Streaming ON - open Huawei Health and keep its home screen visible.",
                    Toast.LENGTH_LONG
                ).show()
            }
        } else {
            Toast.makeText(this, "Streaming stopped.", Toast.LENGTH_SHORT).show()
        }
    }

    // ------------------------------------------------------------------ status

    private fun refreshStatus() {
        val serviceOn = isAccessibilityServiceEnabled()
        accessibilityStatus.text = if (serviceOn) {
            "ENABLED - watching Huawei Health"
        } else {
            "DISABLED - open accessibility settings below"
        }
        accessibilityStatus.setTextColor(
            if (serviceOn) Color.rgb(21, 128, 61) else Color.rgb(185, 28, 28)
        )

        streamingStatus.text = if (prefs.enabled) {
            "ON - reading only from the Huawei Health screen"
        } else {
            "OFF"
        }
        streamingStatus.setTextColor(
            if (prefs.enabled) Color.rgb(21, 128, 61) else Color.rgb(107, 114, 128)
        )
        serviceToggle.text = getString(
            if (prefs.enabled) R.string.stop_streaming else R.string.start_streaming
        )

        // Prefs are authoritative; the service companion is an instant mirror.
        lastValues.text = prefs.lastPostedValues ?: StreamService.lastPostedValues
            ?: getString(R.string.no_reading)
        val time = if (prefs.lastPostTime > 0L) prefs.lastPostTime else StreamService.lastPostTimeMillis
        lastTime.text = if (time > 0L) "at ${formatTime(time)}" else getString(R.string.dash)
        lastResult.text = "result: " + (prefs.lastResult ?: StreamService.lastResult
            ?: getString(R.string.dash))
    }

    private fun formatTime(millis: Long): String =
        SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(millis))

    private fun isAccessibilityServiceEnabled(): Boolean {
        val enabled = Settings.Secure.getString(
            contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val expected = ComponentName(this, StreamService::class.java)
        return enabled.split(':')
            .mapNotNull { ComponentName.unflattenFromString(it) }
            .any { it == expected }
    }

    // ------------------------------------------------------------------ actions

    private fun openHuaweiHealth() {
        val intent = packageManager.getLaunchIntentForPackage(StreamService.TARGET_PACKAGE)
        if (intent == null) {
            Toast.makeText(this, "Huawei Health is not installed.", Toast.LENGTH_LONG).show()
            return
        }
        startActivity(intent)
    }

    /** Background GET probe; never posts a reading. */
    private fun runConnectionTest(rawEndpoint: String) {
        val endpoint = rawEndpoint.trim().ifBlank { Prefs.DEFAULT_ENDPOINT }
        testResult.text = "Testing ${baseUrl(endpoint)} ..."
        testButton.isEnabled = false

        Thread {
            val detail = probeReceiver(endpoint)
            runOnUiThread {
                testResult.text = detail
                testButton.isEnabled = true
            }
        }.apply { isDaemon = true }.start()
    }

    private fun probeReceiver(endpoint: String): String {
        val base = baseUrl(endpoint)
        if (base.isBlank()) return "Not reachable: endpoint URL is empty."

        val candidates = listOf("$base/api/wearable/latest", "$base/")
        var lastError = "no response"
        for (candidate in candidates) {
            var connection: HttpURLConnection? = null
            try {
                connection = URL(candidate).openConnection() as HttpURLConnection
                connection.requestMethod = "GET"
                connection.connectTimeout = 5_000
                connection.readTimeout = 5_000
                connection.useCaches = false
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                try {
                    stream?.close()
                } catch (ignored: Exception) {
                    // body content is irrelevant for a reachability probe
                }
                if (code in 200..299) {
                    return "Reachable: $candidate -> HTTP $code (GET only, nothing posted)."
                }
                lastError = "$candidate -> HTTP $code"
            } catch (e: Exception) {
                lastError = "$candidate -> ${e.javaClass.simpleName}: ${e.message}"
            } finally {
                try {
                    connection?.disconnect()
                } catch (ignored: Exception) {
                    // nothing useful to do here
                }
            }
        }
        return "Not reachable. Last attempt: $lastError"
    }

    /** Endpoint without the /api/wearable path, for probing the receiver root. */
    private fun baseUrl(endpoint: String): String =
        endpoint.trim().trimEnd('/').removeSuffix("/api/wearable").trimEnd('/')

    companion object {
        private const val REFRESH_MS = 1_000L
    }
}
