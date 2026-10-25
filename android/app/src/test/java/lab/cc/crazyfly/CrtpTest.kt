package lab.cc.crazyfly

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Эталонные байты получены из cflib 0.1.34 (struct.pack в программе для компьютера). */
class CrtpTest {
    private fun hex(text: String) = text.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    @Test
    fun setpointMatchesCflib() {
        assertArrayEquals(hex("3c0000c03f000000c0000040403075"), Crtp.setpoint(1.5, 2.0, 3.0, 30000))
    }

    @Test
    fun stopArmAndRecoveryMatchCflib() {
        assertArrayEquals(hex("7c00"), Crtp.stop())
        assertArrayEquals(hex("7d0000000000"), Crtp.notifySetpointStop())
        val arm = Crtp.arm(true)
        assertArrayEquals(hex("9d0101"), arm[0])
        assertArrayEquals(hex("dc0101"), arm[1])
        assertArrayEquals(hex("9d02"), Crtp.crashRecovery()[0])
    }

    @Test
    fun paramByNameMatchesCflib() {
        val packet = Crtp.setParamByName("flightmode.stabModePitch", 0)
        assertArrayEquals(hex("2f00666c696768746d6f646500737461624d6f64655069746368000800"), packet)
    }

    @Test(expected = IllegalArgumentException::class)
    fun thrustOutOfRangeIsRejected() {
        Crtp.setpoint(0.0, 0.0, 0.0, 70000)
    }

    @Test
    fun setByNameReplyIsParsed() {
        val reply = hex("2f00") + "flightmode".toByteArray() + 0 + "stabModeRoll".toByteArray() + 0 + 0
        assertEquals("flightmode.stabModeRoll" to 0, Crtp.parseSetByNameReply(reply))
        val error = hex("2f00") + "flightmode".toByteArray() + 0 + "nope".toByteArray() + 0 + 2
        assertEquals("flightmode.nope" to 2, Crtp.parseSetByNameReply(error))
        assertNull(Crtp.parseSetByNameReply(hex("3c00")))  // не тот порт
        assertNull(Crtp.parseSetByNameReply(hex("2f00666c")))  // оборван
    }

    private operator fun ByteArray.plus(value: Int): ByteArray = this + value.toByte()
}
