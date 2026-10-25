package lab.cc.crazyfly

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FlightTest {
    private val limits = Limits()
    private val dt = 0.02
    private val hover = Sticks(throttle = 0.4)
    private val low = Sticks(throttle = -1.0)
    private val flying = FlightState(phase = Phase.FLYING, thrust = 30000)

    private fun step(state: FlightState, mode: Mode, command: Command = Command(), sticks: Sticks = hover) =
        Flight.step(state, dt, sticks, command, mode, limits)

    /** Крутит манёвр до конца; возвращает итог и все Output. */
    private fun maneuver(start: FlightState, mode: Mode, command: Command): Pair<FlightState, List<Output>> {
        var state = start
        val outputs = mutableListOf<Output>()
        repeat(500) {
            val (next, output) = step(state, mode, command)
            state = next
            outputs += output
            if (state.phase != Phase.FLIPPING) return state to outputs
        }
        return state to outputs
    }

    @Test
    fun takeoffNeedsThrottleDownAndConfirmedMode() {
        val (refused, message) = step(FlightState(), Mode.MANUAL, Command(takeoff = true))
        assertEquals(Phase.IDLE, refused.phase)
        assertTrue(message.message.contains("газ"))
        var (state, output) = step(FlightState(), Mode.MANUAL, Command(takeoff = true), low)
        assertTrue(output.arm)
        assertEquals(Attitude(0.0, 0.0, 0.0, 0), output.setpoint)
        repeat(60) { state = step(state, Mode.MANUAL, Command(firmwareRate = null), low).first }
        assertEquals(Phase.ARMING, state.phase)  // режим не подтверждён - не летим
        state = step(state, Mode.MANUAL, Command(firmwareRate = false), low).first
        assertEquals(Phase.FLYING, state.phase)
    }

    @Test
    fun armingGivesUpWithoutConfirmation() {
        var state = step(FlightState(), Mode.ACRO, Command(takeoff = true), low).first
        var output = Output()
        repeat(200) {
            if (state.phase == Phase.ARMING) {
                val result = step(state, Mode.ACRO, Command(firmwareRate = false), low)
                state = result.first
                output = result.second
            }
        }
        assertEquals(Phase.IDLE, state.phase)
        assertTrue(output.disarm && output.message.contains("не подтвердил"))
    }

    @Test
    fun manualFlipBoostsSpinsFullTurnAndLevels() {
        val thrust = Flight.throttleToThrust(hover.throttle, limits)
        val (start, first) = step(flying, Mode.MANUAL, Command(flipPitch = 1, firmwareRate = false))
        assertEquals(Phase.FLIPPING, start.phase)
        assertEquals("Сальто вперёд!", first.message)
        val (end, outputs) = maneuver(start, Mode.MANUAL, Command(firmwareRate = true))
        assertEquals(Phase.FLYING, end.phase)
        val spin = outputs.mapNotNull { it.setpoint as? Attitude }.filter { it.rate && it.pitch != 0.0 }
        assertEquals(limits.flipAngle, spin.sumOf { it.pitch } * dt, 1e-6)
        assertTrue(spin.all { it.roll == 0.0 && it.pitch > 0 && it.pitch <= limits.flipRate })
        val recover = outputs[outputs.size - 2].setpoint as Attitude
        assertFalse(recover.rate)  // manual: выравнивание углами, прошивка сама вернёт горизонт
        assertTrue(recover.thrust > thrust)
        assertEquals("Сальто готово!", outputs.last().message)
    }

    @Test
    fun acroRollFlipTurnsAroundRollAxis() {
        val start = step(flying, Mode.ACRO, Command(flipRoll = -1, firmwareRate = true)).first
        val (_, outputs) = maneuver(start, Mode.ACRO, Command(firmwareRate = true))
        val spin = outputs.mapNotNull { it.setpoint as? Attitude }.filter { it.roll != 0.0 }
        assertTrue(spin.isNotEmpty() && spin.all { it.roll < 0 && it.pitch == 0.0 && it.rate })
    }

    @Test
    fun flipWaitsForRateModeAndGivesUp() {
        val start = step(flying, Mode.MANUAL, Command(flipPitch = 1)).first
        val (end, outputs) = maneuver(start, Mode.MANUAL, Command(firmwareRate = false))
        assertEquals(Phase.FLYING, end.phase)
        assertTrue(outputs.dropLast(1).all { (it.setpoint as Attitude).pitch == 0.0 })  // ни одного вращения
        assertTrue(outputs.last().message.contains("отменено"))
    }

    @Test
    fun flipNeedsThrottleAndKillWins() {
        val (state, output) = step(flying, Mode.MANUAL, Command(flipPitch = 1), low)
        assertEquals(Phase.FLYING, state.phase)
        assertNull(output.setpoint)
        val flipping = step(flying, Mode.MANUAL, Command(flipPitch = 1)).first
        val (killed, stop) = step(flipping, Mode.MANUAL, Command(kill = true))
        assertEquals(Phase.KILLED, killed.phase)
        assertEquals(Stop, stop.setpoint)
        assertTrue(stop.disarm)
    }

    @Test
    fun manualDashTiltsWhileHeldThenStickTakesOver() {
        val thrust = Flight.throttleToThrust(hover.throttle, limits)
        var (state, output) = step(flying, Mode.MANUAL, Command(dash = 1))
        assertEquals(Attitude(0.0, limits.dashAngle, 0.0, Math.round(thrust * limits.dashBoost).toInt()), output.setpoint)
        assertEquals("Рывок 45° вперёд!", output.message)
        step(state, Mode.MANUAL, Command(dash = 1)).let { state = it.first; output = it.second }
        assertEquals("", output.message)
        output = step(state, Mode.MANUAL, Command()).second
        assertEquals(0.0, (output.setpoint as Attitude).pitch, 0.0)
        repeat(100) { step(state, Mode.MANUAL, Command(dash = -1)).let { state = it.first; output = it.second } }
        assertEquals(0.0, (output.setpoint as Attitude).pitch, 0.0)  // дольше DASH_MAX_TIME не держим
    }

    @Test
    fun acroDashTurns45AndStays() {
        val (start, first) = step(flying, Mode.ACRO, Command(dash = 1, firmwareRate = true))
        assertEquals(Phase.FLIPPING, start.phase)
        var turned = (first.setpoint as Attitude).pitch * dt
        val (end, outputs) = maneuver(start, Mode.ACRO, Command(dash = 1, firmwareRate = true))
        turned += outputs.dropLast(1).sumOf { (it.setpoint as Attitude).pitch } * dt
        assertEquals(limits.dashAngle, turned, 1e-6)
        assertEquals(Phase.FLYING, end.phase)
        assertEquals(0.0, (outputs.last().setpoint as Attitude).pitch, 0.0)
        assertEquals(Phase.FLYING, step(end, Mode.ACRO, Command(dash = 1, firmwareRate = true)).first.phase)
    }

    @Test
    fun landingRampsThrustDownToStop() {
        var state = flying.copy(thrust = 40000)
        var output = Output()
        repeat(500) {
            if (state.phase.active) {
                val result = step(state, Mode.MANUAL, Command(land = state.phase == Phase.FLYING))
                state = result.first
                output = result.second
            }
        }
        assertEquals(Phase.IDLE, state.phase)
        assertEquals(Stop, output.setpoint)
    }
}
