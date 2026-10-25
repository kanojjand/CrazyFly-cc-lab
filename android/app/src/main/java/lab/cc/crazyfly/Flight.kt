package lab.cc.crazyfly

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/*
 * Логика полёта — та же, что cfpilot/flight.py в программе для компьютера, для режимов manual и FPV (acro).
 * Чистые функции: каждый тик step() получает стики и команды и возвращает новое состояние и что отправить дрону.
 * Знаки — как у cflib send_setpoint: крен + вправо, тангаж + вперёд, поворот + по часовой.
 */

enum class Mode(val label: String) { MANUAL("manual"), ACRO("FPV (acro)") }

enum class Phase(val label: String) {
    IDLE("На земле"),
    ARMING("Арминг"),
    FLYING("Полёт"),
    LANDING("Посадка"),
    FLIPPING("Сальто"),
    KILLED("МОТОРЫ ОСТАНОВЛЕНЫ");

    val active: Boolean get() = this == ARMING || this == FLYING || this == LANDING || this == FLIPPING
}

/** Положение стиков -1..1; throttle -1 — газ в самом низу. */
data class Sticks(val roll: Double = 0.0, val pitch: Double = 0.0, val yaw: Double = 0.0, val throttle: Double = 0.0)

sealed interface Setpoint

data class Attitude(
    val roll: Double,
    val pitch: Double,
    val yawrate: Double,
    val thrust: Int,
    val rate: Boolean = false,  // true — крен/тангаж это угловые скорости, false — углы
) : Setpoint

object Stop : Setpoint {
    override fun toString() = "Stop"
}

data class Command(
    val takeoff: Boolean = false,
    val land: Boolean = false,
    val kill: Boolean = false,
    val flipRoll: Int = 0,  // +1 — бочка вправо, -1 — влево (нажатие)
    val flipPitch: Int = 0,  // +1 — сальто вперёд, -1 — назад
    val dash: Int = 0,  // рывок 45°, пока зажат курок: +1 — вперёд, -1 — назад
    val firmwareRate: Boolean? = null,  // как прошивка сейчас понимает крен/тангаж (подтверждено); null — неизвестно
)

data class Output(
    val setpoint: Setpoint? = null,  // null — в этот тик ничего не шлём
    val arm: Boolean = false,
    val disarm: Boolean = false,
    val message: String = "",
)

data class Limits(
    val maxAngle: Double = 15.0,  // град — наклон стиком в manual
    val maxYawRate: Double = 90.0,  // град/с
    val armDelay: Double = 1.0,  // с
    val thrustMin: Int = 10001,
    val thrustMax: Int = 50000,
    val landThrustRamp: Double = 4.0,  // с — за сколько газ уходит с максимума в ноль при посадке
    val maxAcroRate: Double = 90.0,  // град/с — крен/тангаж в FPV
    val acroExpo: Double = 0.6,
    val acroAcceleration: Double = 240.0,  // град/с²
    val flipRate: Double = 720.0,  // град/с
    val flipAngle: Double = 360.0,  // град
    val flipBoostTime: Double = 0.2,  // с
    val flipBoost: Double = 1.35,
    val flipSpinThrust: Double = 0.6,
    val flipRecoverTime: Double = 0.4,  // с
    val dashAngle: Double = 45.0,  // град
    val dashBoost: Double = 1.3,
    val dashRate: Double = 450.0,  // град/с — доворот на 45° в FPV
)

enum class FlipStage { NONE, BOOST, SPIN, RECOVER }

data class Rates(val roll: Double = 0.0, val pitch: Double = 0.0, val yaw: Double = 0.0)

data class FlightState(
    val phase: Phase = Phase.IDLE,
    val timer: Double = 0.0,
    val thrust: Int = 0,
    val acroRates: Rates = Rates(),
    val flipRoll: Int = 0,
    val flipPitch: Int = 0,
    val flipStage: FlipStage = FlipStage.NONE,
    val flipTarget: Double = 0.0,  // град — на сколько повернуть
    val flipTurned: Double = 0.0,  // град — сколько уже скомандовали
    val flipRate: Double = 0.0,
    val flipBoostThrust: Int = 0,
    val flipSpinThrust: Int = 0,
    val flipRecover: Boolean = true,  // после вращения — выравнивание (у рывка 45° в FPV его нет)
    val dash: Int = 0,  // направление рывка в прошлом тике — чтобы поймать нажатие
    val dashTime: Double = 0.0,
)

typealias Step = Pair<FlightState, Output>

object Flight {
    const val THROTTLE_LOW = -0.9
    private const val MOTOR_OFF_FRACTION = 0.02
    private const val KILL_REPEAT = 0.5
    const val KILL_COOLDOWN = 1.0
    const val MODE_TIMEOUT = 2.0  // с сверх armDelay — ждём, пока дрон подтвердит режим управления
    const val THRUST_CAP = 60000
    const val FLIP_SWITCH_TIMEOUT = 0.6
    const val DASH_MAX_TIME = 1.5
    private const val EPS = 1e-9

    fun clamp(value: Double, low: Double = -1.0, high: Double = 1.0) = max(low, min(high, value))

    fun expo(value: Double, expo: Double) = (1.0 - expo) * value + expo * value * value * value

    fun throttleToThrust(throttle: Double, limits: Limits): Int {
        val fraction = (clamp(throttle) + 1.0) / 2.0
        if (fraction < MOTOR_OFF_FRACTION) return 0
        return (limits.thrustMin + fraction * (limits.thrustMax - limits.thrustMin)).roundToInt()
    }

    /** Один тик. Аварийный стоп главнее всего. */
    fun step(state: FlightState, dt: Double, sticks: Sticks, command: Command, mode: Mode, limits: Limits): Step {
        if (command.kill) {
            return FlightState(phase = Phase.KILLED) to Output(Stop, disarm = state.phase != Phase.KILLED)
        }
        val tick = Tick(dt, sticks, command, mode, limits)
        return when (state.phase) {
            Phase.IDLE -> tick.onGround(state)
            Phase.KILLED -> tick.killed(state)
            Phase.ARMING -> tick.arming(state)
            Phase.FLYING -> tick.flying(state)
            Phase.LANDING -> tick.landing(state)
            Phase.FLIPPING -> tick.flipping(state)
        }
    }

    private class Tick(val dt: Double, val sticks: Sticks, val command: Command, val mode: Mode, val limits: Limits) {
        val acro get() = mode == Mode.ACRO

        fun unlock() = Attitude(0.0, 0.0, 0.0, 0, rate = acro)  // нулевая тяга снимает защиту прошивки от газа

        fun motorsOff(message: String = ""): Step = FlightState() to Output(Stop, disarm = true, message = message)

        fun onGround(state: FlightState): Step {
            if (!command.takeoff) return state to Output()
            if (sticks.throttle > THROTTLE_LOW) return state to Output(message = "Опусти газ в самый низ, потом взлетай")
            return FlightState(phase = Phase.ARMING) to Output(unlock(), arm = true)
        }

        fun killed(state: FlightState): Step {
            val timer = state.timer + dt
            if (timer + EPS >= KILL_COOLDOWN) return onGround(state.copy(timer = timer))
            val message = if (command.takeoff) "Подожди секунду после аварийной остановки" else ""
            return state.copy(timer = timer) to Output(if (timer < KILL_REPEAT) Stop else null, message = message)
        }

        fun arming(state: FlightState): Step {
            if (command.land) return motorsOff()
            if (sticks.throttle > THROTTLE_LOW) return motorsOff("Газ подняли во время арминга — взлёт отменён")
            val timer = state.timer + dt
            if (timer + EPS < limits.armDelay) return state.copy(timer = timer) to Output(unlock())
            if (command.firmwareRate == acro) return FlightState(phase = Phase.FLYING) to Output(unlock())
            if (timer > limits.armDelay + MODE_TIMEOUT) {
                return motorsOff("Дрон не подтвердил режим управления — проверь связь и прошивку")
            }
            return state.copy(timer = timer) to Output(unlock(), message = "Жду подтверждения режима от дрона…")
        }

        fun flying(state: FlightState): Step {
            if (command.land) return landing(state.copy(phase = Phase.LANDING))
            if (command.flipRoll != 0 || command.flipPitch != 0) return startFlip(state)
            val (dashState, dashOutput) = dash(state)
            if (dashOutput != null) return dashState to dashOutput
            val thrust = throttleToThrust(sticks.throttle, limits)
            if (!acro) return dashState.copy(thrust = thrust) to Output(angles(thrust))
            val rates = if (thrust == 0) Rates() else Rates(
                softRate(dashState.acroRates.roll, expo(clamp(sticks.roll), limits.acroExpo) * limits.maxAcroRate),
                softRate(dashState.acroRates.pitch, expo(clamp(sticks.pitch), limits.acroExpo) * limits.maxAcroRate),
                softRate(dashState.acroRates.yaw, expo(clamp(sticks.yaw), limits.acroExpo) * limits.maxYawRate),
            )
            val setpoint = Attitude(rates.roll, rates.pitch, rates.yaw, thrust, rate = true)
            return dashState.copy(thrust = thrust, acroRates = rates) to Output(setpoint)
        }

        fun angles(thrust: Int) = Attitude(sticks.roll * limits.maxAngle, sticks.pitch * limits.maxAngle,
                                           sticks.yaw * limits.maxYawRate, thrust)

        /** Плавный набор вращения; отпустил или уменьшил стик — сразу. */
        fun softRate(previous: Double, target: Double): Double {
            val from = if (previous * target < 0) 0.0 else previous
            if (abs(target) <= abs(from)) return target
            val change = limits.acroAcceleration * max(0.0, dt)
            return from + clamp(target - from, -change, change)
        }

        fun landing(state: FlightState): Step {
            // Без датчика высоты знать, где земля, нельзя — просто плавно убираем газ.
            val thrust = (state.thrust - limits.thrustMax / limits.landThrustRamp * dt).roundToInt()
            if (thrust <= limits.thrustMin) return motorsOff()
            val setpoint = if (acro) Attitude(0.0, 0.0, 0.0, thrust, rate = true) else angles(thrust)
            return state.copy(thrust = thrust) to Output(setpoint)
        }

        fun boosted(thrust: Int, factor: Double) = max(limits.thrustMin, min(THRUST_CAP, (thrust * factor).roundToInt()))

        fun startFlip(state: FlightState): Step {
            if (command.flipRoll != 0 && command.flipPitch != 0) {
                return state to Output(message = "Сальто: нажми одну стрелку")
            }
            val thrust = throttleToThrust(sticks.throttle, limits)
            if (thrust == 0) return state to Output(message = "Сальто: сначала подними газ")
            val flip = state.copy(
                phase = Phase.FLIPPING, timer = 0.0, thrust = thrust,
                flipRoll = command.flipRoll, flipPitch = command.flipPitch, flipStage = FlipStage.BOOST,
                flipTarget = limits.flipAngle, flipTurned = 0.0, flipRate = limits.flipRate,
                flipBoostThrust = boosted(thrust, limits.flipBoost), flipSpinThrust = boosted(thrust, limits.flipSpinThrust),
                flipRecover = true,
            )
            val label = when {
                command.flipPitch > 0 -> "Сальто вперёд!"
                command.flipPitch < 0 -> "Сальто назад!"
                command.flipRoll > 0 -> "Бочка вправо!"
                else -> "Бочка влево!"
            }
            return flip to flipOutput(flip).copy(message = label)
        }

        /**
         * Рывок 45°, пока зажат курок. manual — наклон 45° с добавкой тяги, отпустил — снова стик;
         * FPV — быстрый доворот на 45° и дальше пилот выравнивает сам. null вместо Output — рывка нет.
         */
        fun dash(state: FlightState): Pair<FlightState, Output?> {
            val direction = command.dash
            val pressed = direction != 0 && direction != state.dash
            val held = if (direction != 0 && !pressed) state.dashTime + dt else 0.0
            val next = state.copy(dash = direction, dashTime = held)
            if (direction == 0) return next to null
            val label = if (direction > 0) "Рывок 45° вперёд!" else "Рывок 45° назад!"
            val thrust = throttleToThrust(sticks.throttle, limits)
            if (thrust == 0) return next to null
            if (acro) {
                if (!pressed) return next to null
                val tilt = next.copy(
                    phase = Phase.FLIPPING, timer = 0.0, thrust = thrust, flipRoll = 0, flipPitch = direction,
                    flipStage = FlipStage.SPIN, flipTarget = limits.dashAngle, flipTurned = 0.0, flipRate = limits.dashRate,
                    flipBoostThrust = thrust, flipSpinThrust = thrust, flipRecover = false,
                )
                val (spun, output) = spin(tilt)
                return spun to output.copy(message = label)
            }
            if (next.dashTime > DASH_MAX_TIME) return next to null
            val setpoint = Attitude(sticks.roll * limits.maxAngle, direction * limits.dashAngle,
                                    sticks.yaw * limits.maxYawRate, boosted(thrust, limits.dashBoost))
            return next.copy(thrust = thrust) to Output(setpoint, message = if (pressed) label else "")
        }

        /** Подскок и выравнивание. «0, 0» по скоростям безопасно, даже пока прошивка ещё в режиме углов: это «ровно». */
        fun flipOutput(state: FlightState): Output {
            if (state.flipStage == FlipStage.RECOVER && !acro) {
                return Output(Attitude(0.0, 0.0, 0.0, state.flipBoostThrust))
            }
            return Output(Attitude(0.0, 0.0, 0.0, state.flipBoostThrust, rate = true))
        }

        /** Вращение: угол набираем по скорости, последний пакет — ровно на остаток до цели. */
        fun spin(state: FlightState): Step {
            val left = state.flipTarget - state.flipTurned
            if (left <= EPS) {
                if (!state.flipRecover) return endFlip(state, "")
                val recover = state.copy(flipStage = FlipStage.RECOVER, timer = 0.0)
                return recover to flipOutput(recover)
            }
            val rate = if (dt > 0) min(state.flipRate, left / dt) else state.flipRate
            val next = state.copy(flipTurned = state.flipTurned + rate * dt)
            val setpoint = Attitude(state.flipRoll * rate, state.flipPitch * rate, 0.0, state.flipSpinThrust, rate = true)
            return next to Output(setpoint)
        }

        fun endFlip(state: FlightState, message: String): Step {
            val flying = state.copy(phase = Phase.FLYING, timer = 0.0, flipRoll = 0, flipPitch = 0,
                                    flipStage = FlipStage.NONE, flipTurned = 0.0, acroRates = Rates())
            val calm = Tick(dt, sticks, Command(dash = command.dash, firmwareRate = command.firmwareRate), mode, limits)
            val (next, output) = calm.flying(flying)
            return next to output.copy(message = message.ifEmpty { output.message })
        }

        fun flipping(state: FlightState): Step {
            if (command.land) return landing(state.copy(phase = Phase.LANDING))
            if (throttleToThrust(sticks.throttle, limits) == 0) return endFlip(state, "Газ в ноль — манёвр прерван")
            if (state.flipStage == FlipStage.SPIN) return spin(state)
            val timer = state.timer + dt
            if (state.flipStage == FlipStage.BOOST) {
                if (timer + EPS >= limits.flipBoostTime && command.firmwareRate == true) {
                    return spin(state.copy(flipStage = FlipStage.SPIN, timer = 0.0))
                }
                if (timer > limits.flipBoostTime + FLIP_SWITCH_TIMEOUT) {
                    return endFlip(state, "Дрон не перешёл на угловые скорости — сальто отменено")
                }
            } else if (timer + EPS >= limits.flipRecoverTime) {
                return endFlip(state, "Сальто готово!")
            }
            val next = state.copy(timer = timer)
            return next to flipOutput(next)
        }
    }
}
