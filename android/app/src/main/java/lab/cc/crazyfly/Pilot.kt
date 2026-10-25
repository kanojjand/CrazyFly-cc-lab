package lab.cc.crazyfly

import kotlin.math.abs

/** Кнопки DualSense (на Android так же называются у любого геймпада: ✕ = A, ○ = B, □ = X, △ = Y). */
enum class Button { CROSS, CIRCLE, SQUARE, TRIANGLE, L1, R1, OPTIONS, UP, DOWN, LEFT, RIGHT }

/** Снимок пульта. Оси -1..1 как у Android: Y вниз = +1; курки 0..1. */
data class PadState(
    val leftX: Double = 0.0,
    val leftY: Double = 0.0,
    val rightX: Double = 0.0,
    val rightY: Double = 0.0,
    val l2: Double = 0.0,
    val r2: Double = 0.0,
    val buttons: Set<Button> = emptySet(),
)

/**
 * Пилот на телефоне: пульт -> команды -> логика полёта. Раскладка как в программе для компьютера (Mode 2):
 * левый стик ↕ — газ, правый — наклон, L1/R1 — поворот, ✕ — взлёт, □ — посадка, △ — стоп моторов,
 * ○ — удержание газа (manual), Options — manual ↔ FPV на земле, стрелки — сальто, R2/L2 — рывок 45°.
 */
class Pilot(private val limits: Limits = Limits()) {
    companion object {
        const val DEADZONE = 0.05
        const val EXPO = 0.3
        const val TRIGGER_ON = 0.5
        const val HOLD_DEADZONE = 0.15  // в удержании стик газа у центра ничего не меняет
        const val HOLD_THROTTLE_RATE = 0.3  // ход газа в секунду, если в удержании стик газа отклонён до упора
        const val CATCH_BAND = 0.1  // после удержания стик снова берёт газ, когда подходит ближе этого
        const val MESSAGE_SECONDS = 4.0

        fun deadzone(value: Double, zone: Double): Double {
            val magnitude = abs(value)
            if (magnitude <= zone) return 0.0
            val scaled = (magnitude - zone) / (1.0 - zone)
            return if (value > 0) scaled else -scaled
        }

        fun shaped(value: Double) = Flight.expo(deadzone(Flight.clamp(value), DEADZONE), EXPO)

        fun sticksOf(pad: PadState): Sticks {
            val yaw = (if (Button.R1 in pad.buttons) 1.0 else 0.0) - (if (Button.L1 in pad.buttons) 1.0 else 0.0)
            return Sticks(
                roll = shaped(pad.rightX),
                pitch = shaped(-pad.rightY),
                yaw = yaw,
                throttle = Flight.clamp(-pad.leftY),  // газ без мёртвой зоны: в manual он абсолютный
            )
        }
    }

    var state = FlightState()
        private set
    var mode = Mode.MANUAL
        private set
    var sticks = Sticks(throttle = -1.0)
        private set
    var holding = false
        private set
    private var message = ""
    private var messageLeft = 0.0
    private var previous: Set<Button>? = null  // null — пульт только что подключили: зажатое не считается нажатием
    private var catching = false
    private var nudgeReady = false
    private var heldThrottle = -1.0
    private var landRequested = false
    private var killRequested = false

    val shownMessage: String get() = if (messageLeft > 0) message else ""

    fun show(text: String) {
        message = text
        messageLeft = MESSAGE_SECONDS
    }

    /** Кнопки на экране телефона. */
    fun requestLand() {
        landRequested = true
    }

    fun requestKill() {
        killRequested = true
    }

    fun toggleMode() {
        if (state.phase.active) {
            show("Режим меняется только на земле — сначала посади дрон")
            return
        }
        mode = if (mode == Mode.MANUAL) Mode.ACRO else Mode.MANUAL
        holding = false
        catching = false
        show(if (mode == Mode.ACRO) "Включён FPV (acro): полное ручное управление" else "Включён manual: наклон стабилизируется")
    }

    /** Связь с дроном пропала: прошивка сама остановит моторы — здесь просто на землю. */
    fun linkLost() {
        if (state.phase.active) show("Связь с дроном потеряна — моторы остановит сам дрон")
        state = FlightState()
        holding = false
        catching = false
    }

    /** Один тик (50 Гц). pad = null — пульта нет: в воздухе это посадка. connected — дрон на связи. */
    fun tick(pad: PadState?, dt: Double, firmwareRate: Boolean?, connected: Boolean = true): Output {
        messageLeft = (messageLeft - dt).coerceAtLeast(0.0)
        val buttons = pad?.buttons ?: emptySet()
        val pressed = previous?.let { buttons - it } ?: emptySet()
        previous = if (pad == null) null else buttons
        if (pad == null && state.phase.active && !landRequested) {
            landRequested = true
            show("Пульт отключился — сажаю дрон")
        }
        if (Button.OPTIONS in pressed) toggleMode()
        if (Button.CIRCLE in pressed) toggleHold()
        sticks = if (pad == null) Sticks(throttle = -1.0) else heldThrottle(sticksOf(pad), dt)
        val flying = state.phase == Phase.FLYING
        val takeoff = Button.CROSS in pressed
        if (takeoff && !connected) show("Сначала подключи дрон")
        val command = Command(
            takeoff = takeoff && connected,
            land = landRequested || Button.SQUARE in pressed,
            kill = killRequested || Button.TRIANGLE in buttons,  // стоп действует, пока зажат
            flipRoll = if (!flying) 0 else pair(Button.RIGHT in pressed, Button.LEFT in pressed),
            flipPitch = if (!flying) 0 else pair(Button.UP in pressed, Button.DOWN in pressed),
            dash = if (pad == null) 0 else pair(pad.r2 > TRIGGER_ON, pad.l2 > TRIGGER_ON),
            firmwareRate = firmwareRate,
        )
        landRequested = false
        killRequested = false
        val (next, output) = Flight.step(state, dt, sticks, command, mode, limits)
        state = next
        if (output.message.isNotEmpty()) show(output.message)
        if (!state.phase.active) {
            holding = false
            catching = false
            heldThrottle = -1.0
        }
        return output
    }

    private fun pair(positive: Boolean, negative: Boolean) = (if (positive) 1 else 0) - (if (negative) 1 else 0)

    private fun toggleHold() {
        when {
            mode == Mode.ACRO -> show("FPV: газ управляется напрямую, удержание отключено")
            state.phase != Phase.FLYING -> show("Удержание газа включается в полёте")
            holding -> {
                holding = false
                catching = true
                show("Удержание выкл — подведи стик газа к текущему газу")
            }
            else -> {
                holding = true
                catching = false
                nudgeReady = false
                heldThrottle = sticks.throttle
                show("Удержание газа: стик можно отпустить, он только подправляет газ")
            }
        }
    }

    /** Удержание: газ стоит, где был, стик газа его только подправляет. После — «подхват». */
    private fun heldThrottle(raw: Sticks, dt: Double): Sticks {
        if (holding) {
            if (abs(raw.throttle) <= HOLD_DEADZONE) nudgeReady = true
            if (nudgeReady) {
                val nudge = deadzone(raw.throttle, HOLD_DEADZONE)
                heldThrottle = Flight.clamp(heldThrottle + nudge * HOLD_THROTTLE_RATE * dt)
            }
            return raw.copy(throttle = heldThrottle)
        }
        if (catching) {
            // Без подхвата стик на пружине в центре резко убавил бы газ — дрон бы рухнул.
            if (abs(raw.throttle - heldThrottle) > CATCH_BAND) return raw.copy(throttle = heldThrottle)
            catching = false
            show("Газ снова у стика")
        }
        return raw
    }
}
