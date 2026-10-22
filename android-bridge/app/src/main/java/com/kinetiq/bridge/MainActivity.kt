package com.kinetiq.bridge

import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.kinetiq.bridge.health.HuaweiHealthDataSource
import com.kinetiq.bridge.health.requestHealthAuth
import com.kinetiq.bridge.net.BridgeUploader
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * KinetiQ bridge status screen.
 *
 * Shows: HMS availability, permission state, last genuine reading,
 * laptop-connection state. Start/Stop controls near-real-time heart-rate
 * polling (documented DataController read APIs); SpO2 is a single-shot
 * "latest available sample" query. No simulated values anywhere in this app —
 * missing data is shown as unavailable/stale/denied/unsupported.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var health: HuaweiHealthDataSource
    private lateinit var uploader: BridgeUploader

    private lateinit var hmsState: TextView
    private lateinit var permState: TextView
    private lateinit var lastReading: TextView
    private lateinit var laptopState: TextView
    private lateinit var banner: TextView
    private lateinit var urlInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var startStopBtn: Button

    private var authState: HuaweiHealthDataSource.AuthState =
        HuaweiHealthDataSource.AuthState.Unknown
    private var polling: Job? = null
    private var lastBpm: Int? = null
    private var lastBpmAtMs: Long = 0

    private val timeFmt = DateTimeFormatter.ofPattern("HH:mm:ss")
        .withZone(ZoneId.systemDefault())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        health = HuaweiHealthDataSource(this)
        uploader = BridgeUploader(this)

        hmsState = findViewById(R.id.hmsState)
        permState = findViewById(R.id.permState)
        lastReading = findViewById(R.id.lastReading)
        laptopState = findViewById(R.id.laptopState)
        banner = findViewById(R.id.banner)
        urlInput = findViewById(R.id.urlInput)
        tokenInput = findViewById(R.id.tokenInput)
        startStopBtn = findViewById(R.id.startStopBtn)

        urlInput.setText(uploader.currentUrl())
        // Token field is always blank on launch: never display a stored secret.
        tokenInput.setText("")

        findViewById<Button>(R.id.authBtn).setOnClickListener { requestHealthAuth() }
        startStopBtn.setOnClickListener { togglePolling() }
        findViewById<Button>(R.id.spo2Btn).setOnClickListener { querySpo2Once() }
        findViewById<Button>(R.id.saveBtn).setOnClickListener { saveConfig() }

        refreshStaticStatus()
        paintIdle("Press Authorise, then Start heart-rate.")
    }

    override fun onDestroy() {
        polling?.cancel() // callback/job cleanup: no polling after destroy.
        polling = null
        super.onDestroy()
    }

    // ------------------------------------------------------------ auth flow

    @Deprecated("Required for the Health Kit authorisation result pattern")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: android.content.Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == HuaweiHealthDataSource.AUTH_REQUEST_CODE) {
            authState = health.handleAuthResult(data)
            when (val s = authState) {
                is HuaweiHealthDataSource.AuthState.Granted -> {
                    permState.text = "permission: GRANTED"
                    paintIdle("Authorised. Start heart-rate when the band is worn.")
                }
                is HuaweiHealthDataSource.AuthState.Denied -> {
                    permState.text = "permission: DENIED"
                    showBanner("Health permission denied (code ${s.errorCode}). " +
                        "Grant access in the Huawei Health app / AppGallery Connect scopes.")
                }
                else -> Unit
            }
        }
    }

    // ------------------------------------------------------------ acquisition

    private fun togglePolling() {
        if (polling?.isActive == true) {
            polling?.cancel()
            polling = null
            startStopBtn.text = "Start heart-rate"
            paintIdle("Stopped. Last genuine reading kept below (never fabricated).")
            return
        }
        if (authState !is HuaweiHealthDataSource.AuthState.Granted) {
            showBanner("Authorise Health Kit access first.")
            return
        }
        startStopBtn.text = "Stop heart-rate"
        polling = lifecycleScope.launch {
            while (isActive) {
                pollOnce()
                delay(5_000L)
            }
        }
    }

    private suspend fun pollOnce() {
        val hr = health.readLatestHeartRate()
        val now = System.currentTimeMillis()
        if (hr.bpm != null && hr.sampleTimeMs != null) {
            lastBpm = hr.bpm
            lastBpmAtMs = hr.sampleTimeMs
            val reading = BridgeUploader.Reading(
                heartRateBpm = hr.bpm,
                spo2Percent = null, // HR path never touches SpO2: stays null here.
                heartRateMode = hr.mode,
                spo2Mode = "unavailable",
                sampleTime = Instant.ofEpochMilli(hr.sampleTimeMs),
            )
            lastReading.text = "HR ${hr.bpm} BPM · ${hr.mode} · " +
                timeFmt.format(Instant.ofEpochMilli(hr.sampleTimeMs))
            hideBanner()
            if (hr.mode == "historical") {
                showBanner("Band data is historical (sample age > 15 s). " +
                    "Wear the band snugly; live values resume when fresh samples arrive.")
            }
            when (val res = uploader.upload(reading)) {
                is BridgeUploader.Result.Sent ->
                    laptopState.text = "laptop: sent ${timeFmt.format(Instant.now())}"
                is BridgeUploader.Result.Failed ->
                    laptopState.text = "laptop: FAILED — ${res.reason}"
                is BridgeUploader.Result.NotConfigured ->
                    laptopState.text = "laptop: not configured (set URL + token)"
            }
        } else {
            lastReading.text = "HR unavailable — " +
                if (now - lastBpmAtMs > 60_000L) "no fresh sample (stale/none)"
                else "waiting for first sample…"
            showBanner("No heart-rate sample available. Check band wear, " +
                "Huawei Health sync, and granted scopes.")
            laptopState.text = "laptop: nothing genuine to send (not sending zeros)"
            // Deliberate: unavailable readings are NOT uploaded.
        }
    }

    private fun querySpo2Once() {
        if (authState !is HuaweiHealthDataSource.AuthState.Granted) {
            showBanner("Authorise Health Kit access first.")
            return
        }
        lifecycleScope.launch {
            laptopState.text = "laptop: querying SpO2…"
            val spo2 = health.readLatestSpo2()
            when (spo2.mode) {
                "unsupported" -> {
                    lastReading.text = "SpO2 unsupported on this device / scope not approved."
                    showBanner("SpO2 unavailable: device or approved scopes do not expose " +
                        HuaweiHealthDataSource.SPO2_DATA_TYPE_NAME + ". HR path unaffected.")
                    uploader.upload(
                        BridgeUploader.Reading(null, null, "unavailable",
                            "unsupported", "unsupported"))
                }
                "unavailable" ->
                    showBanner("No SpO2 sample stored yet. Take an on-demand SpO2 " +
                        "measurement on the Band 6, sync Huawei Health, retry.")
                else -> {
                    lastReading.text = "SpO2 ${spo2.percent}% · ${spo2.mode}"
                    hideBanner()
                    uploader.upload(
                        BridgeUploader.Reading(lastBpm, spo2.percent,
                            if (lastBpm != null) "historical" else "unavailable",
                            spo2.mode,
                            sampleTime = Instant.ofEpochMilli(
                                spo2.sampleTimeMs ?: System.currentTimeMillis())))
                    laptopState.text = "laptop: SpO2 sent ${timeFmt.format(Instant.now())}"
                }
            }
        }
    }

    // ------------------------------------------------------------ config/ui

    private fun saveConfig() {
        val url = urlInput.text.toString()
        val token = tokenInput.text.toString()
        if (url.isBlank() || token.isBlank()) {
            showBanner("Enter both the laptop URL and the shared token.")
            return
        }
        uploader.saveConfig(url, token)
        tokenInput.setText("") // never keep the secret on screen.
        laptopState.text = "laptop: configured — waiting for first upload"
        hideBanner()
    }

    private fun refreshStaticStatus() {
        hmsState.text = "HMS Health Kit: ${health.debugAvailability()}"
        permState.text = "permission: UNKNOWN (press Authorise)"
        laptopState.text =
            if (uploader.isConfigured()) "laptop: configured"
            else "laptop: not configured (set URL + token)"
    }

    private fun paintIdle(msg: String) {
        banner.visibility = View.GONE
        if (lastBpm == null) lastReading.text = "No genuine reading yet. $msg"
    }

    private fun showBanner(msg: String) {
        banner.text = msg
        banner.visibility = View.VISIBLE
    }

    private fun hideBanner() {
        banner.visibility = View.GONE
    }
}
