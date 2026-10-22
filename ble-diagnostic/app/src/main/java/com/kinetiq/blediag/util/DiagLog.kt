package com.kinetiq.blediag.util

import android.content.Context
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * App-private diagnostic log (also written to app-scoped external files so it
 * can be copied off without root: Android/data/com.kinetiq.blediag/files/).
 *
 * Privacy rules enforced here:
 *  - scan results for devices the user did NOT select are never logged;
 *  - device addresses are logged only after explicit user selection;
 *  - no personal data beyond the user-chosen band identifier is written.
 */
object DiagLog {
    private const val TAG = "KinetiQ-BLEDiag"
    private val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)

    fun append(context: Context, line: String) {
        Log.i(TAG, line)
        try {
            val dir = context.getExternalFilesDir(null) ?: return
            File(dir, "kinetiq_ble_diag.log")
                .appendText("${fmt.format(Date())} $line\n")
        } catch (e: Exception) {
            Log.w(TAG, "log write failed: ${e.javaClass.simpleName}")
        }
    }
}
