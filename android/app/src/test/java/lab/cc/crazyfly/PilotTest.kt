package lab.cc.crazyfly

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PilotTest {
    private val dt = 0.02
    private val throttleDown = PadState(leftY = 1.0)  // у Android Y вниз = +1

    private fun Pilot.run(pad: PadState?, ticks: Int, firmwareRate: Boolean? = false): Output {
        var output = Output()
        repeat(ticks) { output = tick(pad, dt, firmwareRate) }
        return output
    }

    private fun flyingPilot(): Pilot {
        val pilot = Pilot()
        pilot.run(throttleDown, 1)
        pilot.run(throttleDown.copy(buttons = setOf(Button.CROSS)), 1)
        pilot.run(throttleDown, 60)
        pilot.run(PadState(leftY = -0.2), 5)
        assertEquals(Phase.FLYING, pilot.state.phase)
        return pilot
    }

    @Test
    fun sticksFollowMode2Layout() {
        val sticks = Pilot.sticksOf(PadState(leftY = -1.0, rightX = 1.0, rightY = -1.0, buttons = setOf(Button.R1)))
        assertEquals(1.0, sticks.throttle, 1e-9)
        assertEquals(1.0, sticks.roll, 1e-9)
        assertEquals(1.0, sticks.pitch, 1e-9)
        assertEquals(1.0, sticks.yaw, 1e-9)
        assertEquals(0.0, Pilot.sticksOf(PadState(rightX = 0.03)).roll, 0.0)  // мёртвая зона
    }

    @Test
    fun takeoffNeedsConnectedDrone() {
        val pilot = Pilot()
        pilot.run(throttleDown, 1)
        pilot.tick(throttleDown.copy(buttons = setOf(Button.CROSS)), dt, null, connected = false)
        assertEquals(Phase.IDLE, pilot.state.phase)
        assertTrue(pilot.shownMessage.contains("подключи"))
    }

    @Test
    fun buttonHeldWhenPadConnectsIsNotAPress() {
        val pilot = Pilot()
        pilot.run(throttleDown.copy(buttons = setOf(Button.CROSS)), 5)
        assertEquals(Phase.IDLE, pilot.state.phase)
    }

    @Test
    fun dpadUpFlipsAndTriangleKills() {
        val pilot = flyingPilot()
        pilot.run(PadState(leftY = -0.2, buttons = setOf(Button.UP)), 1)
        assertEquals(Phase.FLIPPING, pilot.state.phase)
        val output = pilot.run(PadState(leftY = -0.2, buttons = setOf(Button.TRIANGLE)), 1)
        assertEquals(Phase.KILLED, pilot.state.phase)
        assertEquals(Stop, output.setpoint)
    }

    @Test
    fun r2HeldIsDashForward() {
        val pilot = flyingPilot()
        val output = pilot.run(PadState(leftY = -0.2, r2 = 1.0), 1)
        assertEquals(Limits().dashAngle, (output.setpoint as Attitude).pitch, 1e-9)
    }

    @Test
    fun lostPadLandsAndModeSwitchesOnlyOnGround() {
        val pilot = flyingPilot()
        pilot.run(PadState(leftY = -0.2, buttons = setOf(Button.OPTIONS)), 1)
        assertEquals(Mode.MANUAL, pilot.mode)
        pilot.run(null, 1)
        assertEquals(Phase.LANDING, pilot.state.phase)
        pilot.run(null, 300)
        assertEquals(Phase.IDLE, pilot.state.phase)
        pilot.toggleMode()
        assertEquals(Mode.ACRO, pilot.mode)
    }

    @Test
    fun holdKeepsThrottleWhenStickReturnsToCenter() {
        val pilot = flyingPilot()
        pilot.run(PadState(leftY = -0.6), 5)
        pilot.run(PadState(leftY = -0.6, buttons = setOf(Button.CIRCLE)), 1)
        assertTrue(pilot.holding)
        pilot.run(PadState(leftY = 0.0), 10)  // отпустил стик: на пружине он в центре
        assertEquals(0.6, pilot.sticks.throttle, 1e-9)
    }
}
