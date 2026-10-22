package com.kinetiq.streamer

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM tests for the extraction logic. These assert the honesty properties:
 * absent metrics stay null, out-of-range numbers are not readings, and the
 * label windows work the way the Huawei Health tiles are laid out.
 */
class HealthParserTest {

    @Test
    fun parsesHeartRateFromSingleNode() {
        val reading = HealthParser.parse(
            listOf("Heart rate", "72 bpm", "Blood oxygen", "98%", "Stress", "34")
        )
        assertEquals(72, reading.heartRateBpm)
        assertEquals(98f, reading.spo2Percent)
        assertEquals(34, reading.stressScore)
    }

    @Test
    fun parsesHeartRateWhenValueAndUnitAreSplit() {
        val reading = HealthParser.parse(listOf("Heart rate", "72", "bpm"))
        assertEquals(72, reading.heartRateBpm)
    }

    @Test
    fun ignoresOutOfRangeHeartRate() {
        assertNull(HealthParser.parse(listOf("Heart rate", "19 bpm")).heartRateBpm)
        assertNull(HealthParser.parse(listOf("Heart rate", "300 bpm")).heartRateBpm)
        assertNull(HealthParser.parse(listOf("Heart rate", "999 bpm")).heartRateBpm)
    }

    @Test
    fun parsesSpo2FromLabelledValue() {
        val reading = HealthParser.parse(listOf("Blood oxygen", "97%"))
        assertEquals(97f, reading.spo2Percent)
    }

    @Test
    fun parsesSpo2FromBareNumberNextToLabel() {
        val reading = HealthParser.parse(listOf("Blood oxygen", "96"))
        assertEquals(96f, reading.spo2Percent)
    }

    @Test
    fun fallsBackToStandalonePercentage() {
        val reading = HealthParser.parse(listOf("Heart rate", "72 bpm", "95%"))
        assertEquals(95f, reading.spo2Percent)
    }

    @Test
    fun rejectsSpo2OutsideSensorRange() {
        assertNull(HealthParser.parse(listOf("Blood oxygen", "69%")).spo2Percent)
        assertNull(HealthParser.parse(listOf("Blood oxygen", "101%")).spo2Percent)
    }

    @Test
    fun parsesStressFromLabelWindow() {
        val reading = HealthParser.parse(listOf("Stress", "low", "28", "Relax"))
        assertEquals(28, reading.stressScore)
    }

    @Test
    fun missingMetricsStayNullNeverZero() {
        val reading = HealthParser.parse(emptyList())
        assertFalse(reading.hasAny)
        assertNull(reading.heartRateBpm)
        assertNull(reading.spo2Percent)
        assertNull(reading.stressScore)
        assertNull(reading.steps)

        val hrOnly = HealthParser.parse(listOf("Heart rate", "80 bpm"))
        assertTrue(hrOnly.hasAny)
        assertEquals(80, hrOnly.heartRateBpm)
        assertNull(hrOnly.spo2Percent)
        assertNull(hrOnly.stressScore)
        assertNull(hrOnly.steps)
    }

    @Test
    fun parsesStepsFromRealHomepageTile() {
        // Node order captured from the live Health home screen (2026-10-22):
        // the lowercase unit node is also a "steps" label, so only the label's
        // immediate neighbour may be read as the value.
        val reading = HealthParser.parse(
            listOf(
                "Sleep", "Add record", "Steps", "4,181", "steps",
                "Mood", "Stress", "57", "Normal",
                "Heart", "127", "bpm",
                "SpO₂", "100%"
            )
        )
        assertEquals(4181, reading.steps)
        assertEquals(127, reading.heartRateBpm)
        assertEquals(57, reading.stressScore)
        assertEquals(100f, reading.spo2Percent)
    }

    @Test
    fun parsesStepsFromValueAndUnitOnly() {
        assertEquals(4181, HealthParser.parse(listOf("4,181", "steps")).steps)
    }

    @Test
    fun parsesStepsFromInlineDescription() {
        val reading = HealthParser.parse(listOf("Today you walked 7,412 steps"))
        assertEquals(7412, reading.steps)
    }

    @Test
    fun stepUnitAloneIsNotACount() {
        // "steps" is a unit node here; the only number belongs to the stress
        // card and must never be promoted to a step count.
        val reading = HealthParser.parse(
            listOf("Sleep", "6 h 10 min", "steps", "Mood", "Stress", "57")
        )
        assertNull(reading.steps)
        assertEquals(57, reading.stressScore)
    }

    @Test
    fun zeroStepsIsAValidReading() {
        assertEquals(0, HealthParser.parse(listOf("Steps", "0")).steps)
    }

    @Test
    fun rejectsOutOfRangeSteps() {
        assertNull(HealthParser.parse(listOf("Steps", "250,000")).steps)
    }

    @Test
    fun displaysParsedSteps() {
        val display = HealthParser.Reading(127, 100f, 57, 4181).display()
        assertTrue(display.contains("Steps 4,181"))
    }

    @Test
    fun ignoresStressLabelWithoutNumber() {
        assertNull(HealthParser.parse(listOf("Stress", "Your stress level is low")).stressScore)
    }

    @Test
    fun firstValidMatchWins() {
        val reading = HealthParser.parse(listOf("72 bpm", "later 80 bpm"))
        assertEquals(72, reading.heartRateBpm)
    }

    @Test
    fun displayUsesDashForAbsentValues() {
        val display = HealthParser.Reading(72, 98f, null, null).display()
        assertTrue(display.contains("HR 72 bpm"))
        assertTrue(display.contains("SpO2 98%"))
        assertTrue(display.contains("Stress -"))
        assertTrue(display.contains("Steps -"))
    }
}
