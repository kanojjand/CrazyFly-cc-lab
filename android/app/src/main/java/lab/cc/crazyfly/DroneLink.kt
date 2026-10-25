package lab.cc.crazyfly

/**
 * Что и как слать дрону поверх любого канала (здесь — Bluetooth): арминг, режим углы/скорости, setpoint-ы.
 *
 * Режим крена/тангажа задают параметры flightmode.*; запись по имени прошивка подтверждает ответом.
 * Пока подтверждения нет, крен и тангаж уходят нулями: в режиме углов это «ровно», в режиме скоростей —
 * «не вращайся». Новый режим пишется только после подтверждения прошлой записи — иначе неизвестно,
 * какая из двух уже действует в прошивке.
 *
 * send(packet, latestOnly): latestOnly — setpoint, вместо которого можно отправить более свежий.
 */
class DroneLink(private val send: (ByteArray, Boolean) -> Unit, private val clock: () -> Long) {
    companion object {
        val RATE_PARAMS = listOf("flightmode.stabModeRoll", "flightmode.stabModePitch")
        val FIXED_PARAMS = mapOf(
            "flightmode.althold" to 0,
            "flightmode.poshold" to 0,
            "flightmode.posSet" to 0,
            "flightmode.stabModeYaw" to 0,
        )
        const val RESEND_MS = 300L  // параметр без ответа пишем снова
        // Супервизор прошивки ещё ~2 с после последней тяги считает дрон летящим: если в это время команды
        // пропадут или дрон разоружат, он решит, что дрон падает, и заблокирует его до перезагрузки.
        const val SETTLE_MS = 3000L
        const val STOP_REPEAT_MS = 100L

        fun stabMode(rate: Boolean) = if (rate) 0 else 1  // 0 — угловая скорость, 1 — угол
    }

    private val requested = HashMap<String, Int>()
    private val confirmed = HashMap<String, Int>()
    private val sentAt = HashMap<String, Long>()
    private var lastSetpoint: Setpoint? = null
    private var lastLift: Long? = null
    private var lastSent = 0L

    /** Почему дрон отказал (например, не принял параметр) — для экрана. */
    @get:Synchronized
    var error: String? = null
        private set

    /** Подтверждённый режим крена/тангажа: true — скорости, false — углы, null — неизвестно или ещё пишется. */
    @get:Synchronized
    val firmwareRate: Boolean?
        get() {
            val names = RATE_PARAMS + FIXED_PARAMS.keys
            if (names.any { requested[it] == null || confirmed[it] != requested[it] }) return null
            val roll = requested.getValue(RATE_PARAMS[0])
            if (requested.getValue(RATE_PARAMS[1]) != roll) return null
            return roll == stabMode(true)
        }

    @Synchronized
    fun onPacket(packet: ByteArray) {
        val (name, code) = Crtp.parseSetByNameReply(packet) ?: return
        val value = requested[name] ?: return
        if (code == 0) {
            confirmed[name] = value
        } else {
            sentAt.remove(name)  // повторять бесполезно
            error = "Дрон не принял параметр $name (код $code)"
        }
    }

    @Synchronized
    fun execute(output: Output) {
        val now = clock()
        val setpoint = output.setpoint
        if (output.arm) {
            val rate = (setpoint as? Attitude)?.rate ?: false
            error = null
            request(FIXED_PARAMS + RATE_PARAMS.associateWith { stabMode(rate) }, now)
            Crtp.crashRecovery().forEach { send(it, false) }  // упал после стопа, но стоит ровно — снимаем «аварию»
            Crtp.arm(true).forEach { send(it, false) }
        }
        if (output.disarm && !settling(now)) Crtp.arm(false).forEach { send(it, false) }
        resendUnconfirmed(now)
        when {
            setpoint != null -> transmit(matchMode(setpoint, now), now)
            lastSetpoint == Stop && settling(now) && now - lastSent >= STOP_REPEAT_MS -> transmit(Stop, now)
        }
    }

    /** Связь закрывают: моторы стоп и «команд больше не будет». */
    @Synchronized
    fun close() {
        send(Crtp.stop(), true)
        send(Crtp.notifySetpointStop(), false)
    }

    private fun matchMode(setpoint: Setpoint, now: Long): Setpoint {
        if (setpoint !is Attitude) return setpoint
        val wanted = stabMode(setpoint.rate)
        val settled = RATE_PARAMS.all { requested[it] != null && confirmed[it] == requested[it] }
        if (settled && requested[RATE_PARAMS[0]] != wanted) request(RATE_PARAMS.associateWith { wanted }, now)
        return if (firmwareRate == setpoint.rate) setpoint else setpoint.copy(roll = 0.0, pitch = 0.0)
    }

    private fun request(values: Map<String, Int>, now: Long) {
        for ((name, value) in values) {
            if (requested[name] == value && confirmed[name] == value) continue
            requested[name] = value
            writeParam(name, value, now)
        }
    }

    private fun resendUnconfirmed(now: Long) {
        for ((name, value) in requested) {
            val sent = sentAt[name] ?: continue
            if (confirmed[name] != value && now - sent >= RESEND_MS) writeParam(name, value, now)
        }
    }

    private fun writeParam(name: String, value: Int, now: Long) {
        send(Crtp.setParamByName(name, value), false)
        sentAt[name] = now
    }

    private fun transmit(setpoint: Setpoint, now: Long) {
        val packet = when (setpoint) {
            is Attitude -> Crtp.setpoint(setpoint.roll, setpoint.pitch, setpoint.yawrate, setpoint.thrust)
            Stop -> Crtp.stop()
        }
        send(packet, true)
        lastSetpoint = setpoint
        lastSent = now
        if (setpoint is Attitude && setpoint.thrust > 0) lastLift = now
    }

    private fun settling(now: Long) = lastLift?.let { now - it < SETTLE_MS } ?: false
}
