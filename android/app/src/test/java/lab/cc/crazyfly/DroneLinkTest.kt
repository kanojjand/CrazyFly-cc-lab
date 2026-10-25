package lab.cc.crazyfly

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DroneLinkTest {
    private var now = 1000L
    private val sent = mutableListOf<ByteArray>()
    private val link = DroneLink({ packet, _ -> sent += packet }) { now }

    private fun reply(name: String, code: Int = 0): ByteArray {
        val (group, param) = name.split(".")
        return byteArrayOf(Crtp.header(2, 3), 0) + group.toByteArray() + byteArrayOf(0) + param.toByteArray() +
            byteArrayOf(0, code.toByte())
    }

    private fun confirmAll(values: Map<String, Int>) = values.keys.forEach { link.onPacket(reply(it)) }

    private val rateParams = DroneLink.RATE_PARAMS.associateWith { 0 }
    private val angleParams = DroneLink.RATE_PARAMS.associateWith { 1 }

    private fun lastSetpoint() = sent.last { it[0] == Crtp.header(3, 0) }

    private fun wrote(name: String, value: Int) = sent.any { it.contentEquals(Crtp.setParamByName(name, value)) }

    @Test
    fun armingWritesModeParamsAndArms() {
        link.execute(Output(Attitude(0.0, 0.0, 0.0, 0), arm = true))
        assertTrue(wrote("flightmode.stabModePitch", 1))
        assertTrue(wrote("flightmode.althold", 0))
        assertTrue(sent.any { it.contentEquals(Crtp.arm(true)[0]) })
        assertNull(link.firmwareRate)
        confirmAll(DroneLink.FIXED_PARAMS + angleParams)
        assertEquals(false, link.firmwareRate)
    }

    @Test
    fun rateCommandsWaitForConfirmationAndSendZerosMeanwhile() {
        link.execute(Output(Attitude(0.0, 0.0, 0.0, 0), arm = true))
        confirmAll(DroneLink.FIXED_PARAMS + angleParams)
        sent.clear()
        link.execute(Output(Attitude(0.0, 720.0, 0.0, 30000, rate = true)))
        assertTrue(wrote("flightmode.stabModePitch", 0))
        assertArrayEquals(Crtp.setpoint(0.0, 0.0, 0.0, 30000), lastSetpoint())  // ещё углы: ровно
        confirmAll(rateParams)
        link.execute(Output(Attitude(0.0, 720.0, 0.0, 30000, rate = true)))
        assertArrayEquals(Crtp.setpoint(0.0, 720.0, 0.0, 30000), lastSetpoint())
    }

    @Test
    fun newModeIsWrittenOnlyAfterPreviousWriteIsConfirmed() {
        link.execute(Output(Attitude(0.0, 0.0, 0.0, 0, rate = true), arm = true))
        confirmAll(DroneLink.FIXED_PARAMS + rateParams)
        link.execute(Output(Attitude(0.0, 0.0, 0.0, 30000)))  // выравнивание: просим углы
        sent.clear()
        link.execute(Output(Attitude(0.0, 720.0, 0.0, 30000, rate = true)))  // углы не подтверждены, а просят скорости
        assertTrue(sent.none { it[0] == Crtp.header(2, 3) })  // новой записи нет
        assertArrayEquals(Crtp.setpoint(0.0, 0.0, 0.0, 30000), lastSetpoint())
        confirmAll(angleParams)
        link.execute(Output(Attitude(0.0, 720.0, 0.0, 30000, rate = true)))
        assertTrue(wrote("flightmode.stabModeRoll", 0))
        assertArrayEquals(Crtp.setpoint(0.0, 0.0, 0.0, 30000), lastSetpoint())
    }

    @Test
    fun unconfirmedParamIsResentAndRefusalIsReported() {
        link.execute(Output(Attitude(0.0, 0.0, 0.0, 0), arm = true))
        sent.clear()
        now += DroneLink.RESEND_MS
        link.execute(Output())
        assertTrue(wrote("flightmode.stabModeRoll", 1))
        link.onPacket(reply("flightmode.posSet", 2))
        assertTrue(link.error!!.contains("flightmode.posSet"))
        assertNull(link.firmwareRate)
    }

    @Test
    fun afterThrustStopIsRepeatedAndDisarmWaits() {
        link.execute(Output(Attitude(0.0, 0.0, 0.0, 0), arm = true))
        confirmAll(DroneLink.FIXED_PARAMS + angleParams)
        link.execute(Output(Attitude(0.0, 0.0, 0.0, 30000)))
        sent.clear()
        link.execute(Output(Stop, disarm = true))
        assertTrue(sent.none { it.contentEquals(Crtp.arm(false)[0]) })  // разоружить сейчас = блокировка прошивкой
        sent.clear()
        now += DroneLink.STOP_REPEAT_MS
        link.execute(Output())
        assertArrayEquals(Crtp.stop(), sent.single())
        now += DroneLink.SETTLE_MS
        sent.clear()
        link.execute(Output())
        assertTrue(sent.isEmpty())
    }
}
