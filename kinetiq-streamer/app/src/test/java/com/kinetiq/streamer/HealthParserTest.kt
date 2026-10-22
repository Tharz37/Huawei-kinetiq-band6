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

        val hrOnly = HealthParser.parse(listOf("Heart rate", "80 bpm"))
        assertTrue(hrOnly.hasAny)
        assertEquals(80, hrOnly.heartRateBpm)
        assertNull(hrOnly.spo2Percent)
        assertNull(hrOnly.stressScore)
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
        val display = HealthParser.Reading(72, 98f, null).display()
        assertTrue(display.contains("HR 72 bpm"))
        assertTrue(display.contains("SpO2 98%"))
        assertTrue(display.contains("Stress -"))
    }
}
