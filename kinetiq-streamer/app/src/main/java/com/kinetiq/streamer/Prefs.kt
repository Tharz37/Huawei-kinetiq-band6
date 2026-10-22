package com.kinetiq.streamer

import android.content.Context
import android.content.SharedPreferences

/**
 * Configuration and the last POST outcome, stored in SharedPreferences so both
 * the Activity and the AccessibilityService see the same values.
 *
 * Honesty note: only the endpoint/token are configurable. The payload's
 * source, transport and simulated flag are hard-coded in [StreamService].
 */
class Prefs(context: Context) {

    private val sp: SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var endpoint: String
        get() = sp.getString(KEY_ENDPOINT, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_ENDPOINT
        set(value) = sp.edit().putString(KEY_ENDPOINT, value.trim()).apply()

    var token: String
        get() = sp.getString(KEY_TOKEN, null) ?: DEFAULT_TOKEN
        set(value) = sp.edit().putString(KEY_TOKEN, value.trim()).apply()

    /** Master switch honoured by [StreamService]. */
    var enabled: Boolean
        get() = sp.getBoolean(KEY_ENABLED, false)
        set(value) = sp.edit().putBoolean(KEY_ENABLED, value).apply()

    var lastResult: String?
        get() = sp.getString(KEY_LAST_RESULT, null)
        set(value) = sp.edit().putString(KEY_LAST_RESULT, value).apply()

    var lastPostedValues: String?
        get() = sp.getString(KEY_LAST_VALUES, null)
        set(value) = sp.edit().putString(KEY_LAST_VALUES, value).apply()

    var lastPostTime: Long
        get() = sp.getLong(KEY_LAST_TIME, 0L)
        set(value) = sp.edit().putLong(KEY_LAST_TIME, value).apply()

    fun recordPost(result: String, values: String, timeMillis: Long) {
        sp.edit()
            .putString(KEY_LAST_RESULT, result)
            .putString(KEY_LAST_VALUES, values)
            .putLong(KEY_LAST_TIME, timeMillis)
            .apply()
    }

    companion object {
        const val FILE = "kinetiq_streamer"

        /** Matches post_healthapp.py defaults. 127.0.0.1 works over `adb reverse`. */
        const val DEFAULT_ENDPOINT = "http://127.0.0.1:8000/api/wearable"
        const val DEFAULT_TOKEN = "poc-demo"

        private const val KEY_ENDPOINT = "endpoint"
        private const val KEY_TOKEN = "token"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_LAST_RESULT = "last_result"
        private const val KEY_LAST_VALUES = "last_values"
        private const val KEY_LAST_TIME = "last_time"
    }
}
