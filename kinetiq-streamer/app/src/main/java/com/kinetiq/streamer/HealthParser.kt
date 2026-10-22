package com.kinetiq.streamer

import java.util.Locale

/**
 * Extracts heart rate, SpO2 and stress from the text nodes of the Huawei
 * Health home screen.
 *
 * HONESTY RULES
 *  - There are no fallback/default numbers. A metric that is not present on
 *    the screen is `null`, never 0.
 *  - Range checks mirror the real sensor envelope; out-of-range numbers are
 *    treated as "not a reading" and skipped.
 *  - Only the first VALID match per metric is used.
 *
 * The parser is pure Kotlin with no Android dependencies so it can be unit
 * tested on the JVM (see HealthParserTest).
 */
object HealthParser {

    const val HR_MIN = 20
    const val HR_MAX = 260
    const val SPO2_MIN = 70
    const val SPO2_MAX = 100
    const val STRESS_MIN = 0
    const val STRESS_MAX = 100

    /** Number of texts after a label node that are searched for its value. */
    private const val LABEL_WINDOW = 3

    data class Reading(
        val heartRateBpm: Int?,
        val spo2Percent: Float?,
        val stressScore: Int?
    ) {
        val hasAny: Boolean
            get() = heartRateBpm != null || spo2Percent != null || stressScore != null

        /** Log/prefs form, e.g. `hr=72 spo2=98.0 stress=null`. */
        fun summary(): String =
            "hr=${heartRateBpm ?: "null"} spo2=${spo2Percent ?: "null"} " +
                "stress=${stressScore ?: "null"}"

        /** Screen form, e.g. `HR 72 bpm   SpO2 98%   Stress 34`. */
        fun display(): String {
            val hr = heartRateBpm?.let { "$it bpm" } ?: "-"
            val spo2 = spo2Percent?.let {
                val format = if (it % 1f == 0f) "%.0f%%" else "%.1f%%"
                String.format(Locale.US, format, it)
            } ?: "-"
            val stress = stressScore?.toString() ?: "-"
            return "HR $hr   SpO2 $spo2   Stress $stress"
        }
    }

    // "72 bpm" in one node.
    private val HR_REGEX = Regex("""\b(\d{2,3})\s*bpm\b""", RegexOption.IGNORE_CASE)
    // Value in/after an oxygen label; percent sign optional because Health's
    // tile often shows the number on its own.
    private val SPO2_VALUE_REGEX = Regex("""\b(\d{2,3})(?!\d)\s*%?""")
    // Last-resort SpO2: a standalone percentage.
    private val PERCENT_REGEX = Regex("""\b(\d{2,3})(?!\d)\s*%""")
    // Standalone stress number; word boundaries avoid chopping digits out of
    // years or node ids.
    private val STRESS_VALUE_REGEX = Regex("""\b(\d{1,3})(?!\d)\b""")

    fun parse(texts: List<String>): Reading {
        val clean = texts.map { it.trim() }.filter { it.isNotEmpty() }
        return Reading(
            heartRateBpm = findHeartRate(clean),
            spo2Percent = findSpo2(clean),
            stressScore = findStress(clean)
        )
    }

    private fun findHeartRate(texts: List<String>): Int? {
        for (text in texts) {
            for (match in HR_REGEX.findAll(text)) {
                val value = match.groupValues[1].toIntOrNull() ?: continue
                if (value in HR_MIN..HR_MAX) return value
            }
        }
        // Health sometimes splits the value and the unit into sibling nodes
        // ("72", "bpm"); retry on a joined view so that still matches.
        val joined = texts.joinToString(" ")
        for (match in HR_REGEX.findAll(joined)) {
            val value = match.groupValues[1].toIntOrNull() ?: continue
            if (value in HR_MIN..HR_MAX) return value
        }
        return null
    }

    private fun findSpo2(texts: List<String>): Float? {
        val labelIndex = texts.indexOfFirst { text ->
            val lower = text.lowercase(Locale.US)
            lower.contains("oxygen") || lower.contains("spo")
        }
        if (labelIndex >= 0) {
            val last = minOf(texts.size - 1, labelIndex + LABEL_WINDOW)
            for (index in labelIndex..last) {
                for (match in SPO2_VALUE_REGEX.findAll(texts[index])) {
                    val value = match.groupValues[1].toIntOrNull() ?: continue
                    if (value in SPO2_MIN..SPO2_MAX) return value.toFloat()
                }
            }
        }
        // No usable labelled value: accept a standalone percentage tile.
        for (text in texts) {
            for (match in PERCENT_REGEX.findAll(text)) {
                val value = match.groupValues[1].toIntOrNull() ?: continue
                if (value in SPO2_MIN..SPO2_MAX) return value.toFloat()
            }
        }
        return null
    }

    private fun findStress(texts: List<String>): Int? {
        val labelIndex = texts.indexOfFirst { it.lowercase(Locale.US).contains("stress") }
        if (labelIndex < 0) return null
        val last = minOf(texts.size - 1, labelIndex + LABEL_WINDOW)
        for (index in labelIndex..last) {
            for (match in STRESS_VALUE_REGEX.findAll(texts[index])) {
                val value = match.groupValues[1].toIntOrNull() ?: continue
                if (value in STRESS_MIN..STRESS_MAX) return value
            }
        }
        return null
    }
}
