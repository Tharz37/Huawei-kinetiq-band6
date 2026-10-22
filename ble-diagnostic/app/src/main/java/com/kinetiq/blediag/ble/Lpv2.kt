package com.kinetiq.blediag.ble

/**
 * Huawei Link Protocol v2 wire-format diagnostics.
 *
 * Based on community research (quantified-student wiki; zyv/huawei-lpv2, MIT),
 * documented in docs/RESEARCH_LPV2.md. Frame shape:
 *
 *   0x5A LLLL 0x00 BODY CRC16
 *   BODY = SS CC TLV…     TLV = TAG LEN(VarInt) VAL ; TAG MSB set = nested
 *   CRC16 = CRC-CCITT/XMODEM (poly 0x1021, init 0x0000, xor 0x0000)
 *   over bytes 0x5A..BODY inclusive.
 *
 * This class performs PASSIVE frame detection/annotation only. It never
 * interprets a decoded value as a health measurement.
 */
object Lpv2 {

    private const val MAGIC = 0x5A

    /** CRC-16/XMODEM as documented; matches the wiki snippet's semantics.
     *  Validated against the wiki's example packet (link-params request,
     *  stated CRC 0xf13b) — see docs/RESEARCH_LPV2.md §verified stages. */
    fun crc16(data: ByteArray, offset: Int = 0, length: Int = data.size - offset): Int {
        var crc = 0
        var i = offset
        while (i < offset + length) {
            crc = crc xor ((data[i].toInt() and 0xFF) shl 8)
            repeat(8) {
                crc = if (crc and 0x8000 != 0) ((crc shl 1) xor 0x1021) and 0xFFFF
                      else (crc shl 1) and 0xFFFF
            }
            i++
        }
        return crc and 0xFFFF
    }

    data class Frame(
        val bodyLength: Int,
        val serviceId: Int,
        val commandId: Int,
        val tlvTags: List<Int>,
        val crcValid: Boolean,
        val rawHex: String,
    )

    /**
     * Detect + annotate a single non-sliced LPv2 frame if [payload] starts
     * with 0x5A and passes the CRC. Sliced / multi frames are DETECTED and
     * labelled as such (not decoded) so they can never be misread.
     */
    fun classify(payload: ByteArray): String {
        if (payload.size < 3 || (payload[0].toInt() and 0xFF) != MAGIC) {
            return "not-lpv2 (${hex(payload, 24)}…)"
        }
        val len = ((payload[1].toInt() and 0xFF) shl 8) or (payload[2].toInt() and 0xFF)
        val const = payload[3].toInt() and 0xFF
        if (const != 0x00) {
            return "lpv2-SLICED/multi (len=$len flag=0x${hex(payload, 4)}) — not decoded"
        }
        if (payload.size < 6) return "lpv2-truncated"
        val bodyLen = len - 1
        if (payload.size < 4 + bodyLen + 2) return "lpv2-incomplete (want ${bodyLen + 6}, have ${payload.size})"
        val crcHi = payload[4 + bodyLen].toInt() and 0xFF
        val crcLo = payload[5 + bodyLen].toInt() and 0xFF
        val expected = (crcHi shl 8) or crcLo
        val actual = crc16(payload, 0, 4 + bodyLen)
        if (expected != actual) {
            return "lpv2 CRC BAD (exp=%04x act=%04x)".format(expected, actual)
        }
        // BODY = SS CC TLVs
        val service = payload[4].toInt() and 0xFF
        val command = payload[5].toInt() and 0xFF
        val tags = parseTags(payload, 6, 4 + bodyLen)
        return "lpv2 svc=$service cmd=$command tags=$tags crc=OK"
    }

    /** Best-effort top-level TLV tag enumeration inside [from, until). */
    private fun parseTags(b: ByteArray, from: Int, until: Int): List<Int> {
        val tags = mutableListOf<Int>()
        var i = from
        try {
            while (i < until && tags.size < 16) {
                val tag = b[i].toInt() and 0xFF
                tags.add(tag)
                i++
                if (i >= until) break
                val vlen = readVarInt(b, i, until) ?: break
                i += vlen.bytes + vlen.value
            }
        } catch (e: Exception) {
            // annotation only — never fatal for diagnostics
        }
        return tags
    }

    private class VarIntResult(val bytes: Int, val value: Int)

    private fun readVarInt(b: ByteArray, from: Int, until: Int): VarIntResult? {
        var value = 0
        var bytes = 0
        var i = from
        while (i < until && bytes < 4) {
            val byte = b[i].toInt() and 0xFF
            value = (value shl 7) or (byte and 0x7F)
            bytes++
            i++
            if (byte and 0x80 == 0) return VarIntResult(bytes, value)
        }
        return null
    }

    fun hex(b: ByteArray, max: Int = b.size): String =
        b.take(max).joinToString(" ") { "%02x".format(it) }

    /**
     * Assemble a full LPv2 request frame (0x5A LLLL 00 SS CC TLVs CRC16) for
     * the DOCUMENTED plaintext probes. Length counts 0x00 + svc + cmd + TLVs
     * (verified against live-observed band frames in this session).
     */
    fun frame(service: Int, command: Int, vararg tlvs: Byte): ByteArray {
        val len = 3 + tlvs.size
        val b = ByteArray(4 + len + 2)
        b[0] = MAGIC.toByte()
        b[1] = ((len shr 8) and 0xFF).toByte()
        b[2] = (len and 0xFF).toByte()
        b[3] = 0x00
        b[4] = service.toByte()
        b[5] = command.toByte()
        for (i in tlvs.indices) b[6 + i] = tlvs[i]
        val crc = crc16(b, 0, 6 + tlvs.size)
        b[6 + tlvs.size] = ((crc shr 8) and 0xFF).toByte()
        b[7 + tlvs.size] = (crc and 0xFF).toByte()
        return b
    }
}
