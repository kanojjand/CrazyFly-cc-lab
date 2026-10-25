package lab.cc.crazyfly

/**
 * CRTP поверх Bluetooth LE у Crazyflie (сервис 00000201-1c7f-4f9e-947b-43b7c00a9a08).
 *
 * Пакет до 20 байт пишется целиком в характеристику crtp. Длиннее — двумя кадрами в crtpUp:
 * управляющий байт [старт 0x80 | pid << 5 | длина] и куски пакета. Ответы дрона приходят в crtpDown
 * так же кусками, но там в управляющем байте «длина − 1» (прошивка nRF, ble_crtpdown.c).
 */
class BleFrame(val segmented: Boolean, val bytes: ByteArray)

object BleFraming {
    const val MAX_FRAME = 20
    private const val CHUNK = MAX_FRAME - 1

    /**
     * В crtpUp прошивка nRF берёт поле длины как число байт пакета (ble_crazyflies.c: packet_length = data[0] & 0x1F)
     * и отдаёт ровно столько. Поэтому пишем полную длину: с «длина − 1» потерялся бы последний байт — значение параметра.
     */
    fun frames(packet: ByteArray, pid: Int): List<BleFrame> {
        require(packet.size <= 2 * CHUNK) { "пакет длиннее ${2 * CHUNK} байт" }
        if (packet.size <= MAX_FRAME) return listOf(BleFrame(false, packet))
        val first = byteArrayOf((0x80 or ((pid and 3) shl 5) or packet.size).toByte()) + packet.copyOfRange(0, CHUNK)
        val second = byteArrayOf(((pid and 3) shl 5).toByte()) + packet.copyOfRange(CHUNK, packet.size)
        return listOf(BleFrame(true, first), BleFrame(true, second))
    }
}

/** Сборка ответов из кадров crtpDown. */
class BleReassembler {
    private var pid = -1
    private var length = 0
    private var head = ByteArray(0)

    fun feed(frame: ByteArray): ByteArray? {
        if (frame.isEmpty()) return null
        val control = frame[0].toInt() and 0xFF
        val framePid = (control shr 5) and 3
        val body = frame.copyOfRange(1, frame.size)
        if (control and 0x80 != 0) {
            val total = (control and 0x1F) + 1
            if (total <= body.size) {
                pid = -1
                return body.copyOfRange(0, total)
            }
            pid = framePid
            length = total
            head = body
            return null
        }
        if (framePid != pid) {
            pid = -1  // начало потерялось — этот пакет не собрать
            return null
        }
        pid = -1
        val whole = head + body
        return if (whole.size >= length) whole.copyOfRange(0, length) else null
    }
}
