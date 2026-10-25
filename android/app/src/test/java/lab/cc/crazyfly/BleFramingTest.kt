package lab.cc.crazyfly

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BleFramingTest {
    @Test
    fun shortPacketGoesWholeToCrtpCharacteristic() {
        val setpoint = Crtp.setpoint(0.0, 0.0, 0.0, 0)
        val frames = BleFraming.frames(setpoint, 0)
        assertEquals(1, frames.size)
        assertFalse(frames[0].segmented)
        assertArrayEquals(setpoint, frames[0].bytes)
    }

    @Test
    fun longPacketIsSplitWithFullLengthInControlByte() {
        val packet = Crtp.setParamByName("flightmode.stabModePitch", 1)  // 29 байт
        val frames = BleFraming.frames(packet, 2)
        assertEquals(2, frames.size)
        assertTrue(frames.all { it.segmented && it.bytes.size <= BleFraming.MAX_FRAME })
        // старт | pid 2 | 29 байт: прошивка nRF отдаёт ровно столько байт, сколько записано в поле длины
        assertEquals((0x80 or (2 shl 5) or 29).toByte(), frames[0].bytes[0])
        assertEquals((2 shl 5).toByte(), frames[1].bytes[0])
        val joined = frames[0].bytes.copyOfRange(1, 20) + frames[1].bytes.copyOfRange(1, frames[1].bytes.size)
        assertArrayEquals(packet, joined)
    }

    @Test
    fun downlinkFragmentsAreReassembled() {
        val reply = Crtp.setParamByName("flightmode.stabModeRoll", 0).copyOfRange(0, 26)  // любой длинный пакет
        // Как ble_crtpdown.c: [0x80 | pid<<5 | (длина-1)] + 19 байт, затем [pid<<5] + остаток.
        val first = byteArrayOf((0x80 or (1 shl 5) or (reply.size - 1)).toByte()) + reply.copyOfRange(0, 19)
        val second = byteArrayOf((1 shl 5).toByte()) + reply.copyOfRange(19, reply.size)
        val reassembler = BleReassembler()
        assertNull(reassembler.feed(first))
        assertArrayEquals(reply, reassembler.feed(second))
    }

    @Test
    fun shortDownlinkPacketAndLostStartAreHandled() {
        val reassembler = BleReassembler()
        val short = byteArrayOf(0x3c, 0x01, 0x02)
        assertArrayEquals(short, reassembler.feed(byteArrayOf((0x80 or (short.size - 1)).toByte()) + short))
        assertNull(reassembler.feed(byteArrayOf((3 shl 5).toByte(), 1, 2)))  // продолжение без начала
    }
}
