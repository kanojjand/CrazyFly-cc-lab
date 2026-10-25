package lab.cc.crazyfly

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Пакеты CRTP — байт в байт те же, что шлёт cflib в программе для компьютера. */
object Crtp {
    private const val PORT_PARAM = 0x02
    private const val PORT_COMMANDER = 0x03
    private const val PORT_COMMANDER_GENERIC = 0x07
    private const val PORT_SUPERVISOR = 0x09
    private const val PORT_PLATFORM = 0x0D
    private const val PARAM_MISC_CHANNEL = 3
    private const val PARAM_SET_BY_NAME = 0
    private const val PARAM_TYPE_UINT8 = 0x08
    private const val SUPERVISOR_COMMAND_CHANNEL = 1
    private const val SUPERVISOR_ARM = 1
    private const val SUPERVISOR_RECOVER = 2
    private const val SETPOINT_STOP = 0
    private const val META_NOTIFY_SETPOINT_STOP = 0
    private const val NUL: Byte = 0
    const val MAX_THRUST = 0xFFFF

    fun header(port: Int, channel: Int): Byte = (((port and 0x0F) shl 4) or (3 shl 2) or (channel and 0x03)).toByte()

    private fun packet(port: Int, channel: Int, data: ByteArray): ByteArray = byteArrayOf(header(port, channel)) + data

    private fun littleEndian(size: Int): ByteBuffer = ByteBuffer.allocate(size).order(ByteOrder.LITTLE_ENDIAN)

    /** Крен, тангаж (+ вперёд), скорость поворота, тяга 0..65535. Как прошивка их понимает — задают параметры flightmode. */
    fun setpoint(roll: Double, pitch: Double, yawrate: Double, thrust: Int): ByteArray {
        require(thrust in 0..MAX_THRUST) { "тяга вне 0..$MAX_THRUST: $thrust" }
        val data = littleEndian(14).putFloat(roll.toFloat()).putFloat(-pitch.toFloat()).putFloat(yawrate.toFloat())
            .putShort(thrust.toShort())
        return packet(PORT_COMMANDER, 0, data.array())
    }

    fun stop(): ByteArray = packet(PORT_COMMANDER_GENERIC, 0, byteArrayOf(SETPOINT_STOP.toByte()))

    /** «Дальше команд не будет» — прошивка перестаёт ждать setpoint-ы и не считает это потерей связи. */
    fun notifySetpointStop(): ByteArray =
        packet(PORT_COMMANDER_GENERIC, 1, littleEndian(5).put(META_NOTIFY_SETPOINT_STOP.toByte()).putInt(0).array())

    /** Новые прошивки понимают supervisor, старые — platform; каждая молча пропускает чужой пакет. */
    fun arm(on: Boolean): List<ByteArray> {
        val flag = (if (on) 1 else 0).toByte()
        return listOf(
            packet(PORT_SUPERVISOR, SUPERVISOR_COMMAND_CHANNEL, byteArrayOf(SUPERVISOR_ARM.toByte(), flag)),
            packet(PORT_PLATFORM, 0, byteArrayOf(SUPERVISOR_ARM.toByte(), flag)),
        )
    }

    fun crashRecovery(): List<ByteArray> = listOf(
        packet(PORT_SUPERVISOR, SUPERVISOR_COMMAND_CHANNEL, byteArrayOf(SUPERVISOR_RECOVER.toByte())),
        packet(PORT_PLATFORM, 0, byteArrayOf(SUPERVISOR_RECOVER.toByte())),
    )

    /** Запись uint8-параметра по имени («группа.имя») — без загрузки списка параметров. */
    fun setParamByName(name: String, value: Int): ByteArray {
        val (group, param) = name.split('.', limit = 2)
        val data = byteArrayOf(PARAM_SET_BY_NAME.toByte()) + group.toByteArray() + NUL + param.toByteArray() + NUL +
            PARAM_TYPE_UINT8.toByte() + value.toByte()
        return packet(PORT_PARAM, PARAM_MISC_CHANNEL, data)
    }

    /** Ответ прошивки на setParamByName: [0, группа\0, имя\0, код ошибки] -> («группа.имя», код); иначе null. */
    fun parseSetByNameReply(packet: ByteArray): Pair<String, Int>? {
        if (packet.size < 2) return null
        val header = packet[0].toInt() and 0xFF
        if (header shr 4 != PORT_PARAM || header and 0x03 != PARAM_MISC_CHANNEL) return null
        if (packet[1].toInt() != PARAM_SET_BY_NAME) return null
        val groupEnd = packet.indexOf(0, from = 2) ?: return null
        val nameEnd = packet.indexOf(0, from = groupEnd + 1) ?: return null
        if (nameEnd + 1 >= packet.size) return null
        val group = String(packet, 2, groupEnd - 2)
        val name = String(packet, groupEnd + 1, nameEnd - groupEnd - 1)
        return "$group.$name" to (packet[nameEnd + 1].toInt() and 0xFF)
    }

    private fun ByteArray.indexOf(value: Byte, from: Int): Int? = (from until size).firstOrNull { this[it] == value }
}
