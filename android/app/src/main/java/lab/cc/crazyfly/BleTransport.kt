package lab.cc.crazyfly

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.util.UUID

/**
 * Bluetooth LE-связь с Crazyflie: поиск, подключение, отправка CRTP-кадров, приём ответов.
 *
 * Android разрешает одну операцию GATT за раз, поэтому кадры идут по очереди: служебные (арминг, параметры)
 * — все по порядку, а setpoint — только самый свежий: устаревший, не успевший уйти, просто заменяется.
 * Все вызовы GATT — в главном потоке.
 */
@SuppressLint("MissingPermission")  // разрешения проверяет MainActivity до connect()
class BleTransport(private val context: Context, private val listener: Listener) {
    interface Listener {
        fun onStatus(status: Status, detail: String)
        fun onPacket(packet: ByteArray)
    }

    enum class Status(val label: String) {
        IDLE("не подключён"),
        SCANNING("ищу дрон…"),
        CONNECTING("подключаюсь…"),
        CONNECTED("подключён"),
        LOST("связь потеряна"),
    }

    companion object {
        private val SERVICE = UUID.fromString("00000201-1c7f-4f9e-947b-43b7c00a9a08")
        private val CRTP = UUID.fromString("00000202-1c7f-4f9e-947b-43b7c00a9a08")
        private val CRTP_UP = UUID.fromString("00000203-1c7f-4f9e-947b-43b7c00a9a08")
        private val CRTP_DOWN = UUID.fromString("00000204-1c7f-4f9e-947b-43b7c00a9a08")
        private val CLIENT_CONFIG = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
        private const val NAME = "Crazyflie"
        private const val LOADER_NAME = "Crazyflie Loader"  // загрузчик прошивки — управлять им нельзя
        private const val SCAN_TIMEOUT_MS = 10_000L
        private const val WRITE_TIMEOUT_MS = 100L  // подтверждение записи не пришло — шлём следующий кадр
        private const val RETRY_MS = 5L
    }

    private val main = Handler(Looper.getMainLooper())
    private val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
    private val lock = Any()
    private val control = ArrayDeque<BleFrame>()
    private var latest: BleFrame? = null
    private var busySince: Long? = null
    private var pid = 0
    private var gatt: BluetoothGatt? = null
    private var crtp: BluetoothGattCharacteristic? = null
    private var crtpUp: BluetoothGattCharacteristic? = null
    private val reassembler = BleReassembler()

    @Volatile
    var status = Status.IDLE
        private set

    @Volatile
    var deviceName = ""
        private set

    fun connect() {
        main.post {
            close()
            val scanner = adapter?.bluetoothLeScanner
            when {
                adapter == null -> setStatus(Status.IDLE, "На телефоне нет Bluetooth")
                !adapter.isEnabled || scanner == null -> setStatus(Status.IDLE, "Включи Bluetooth")
                else -> {
                    setStatus(Status.SCANNING, "Включи дрон и поднеси поближе")
                    val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
                    scanner.startScan(null, settings, scan)
                    main.postDelayed(scanTimeout, SCAN_TIMEOUT_MS)
                }
            }
        }
    }

    fun disconnect() {
        main.post { close() }
    }

    /** Пакет CRTP целиком; latestOnly — setpoint: если старый ещё не ушёл, уйдёт этот. */
    fun send(packet: ByteArray, latestOnly: Boolean) {
        synchronized(lock) {
            val frames = BleFraming.frames(packet, pid)
            if (frames.size > 1) pid = (pid + 1) and 3
            if (latestOnly && frames.size == 1) latest = frames[0] else control.addAll(frames)
        }
        main.post { pump() }
    }

    private val scanTimeout = Runnable {
        stopScan()
        if (status == Status.SCANNING) setStatus(Status.IDLE, "Дрон не найден — он включён и рядом?")
    }

    private val scan = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val name = result.scanRecord?.deviceName ?: result.device.name ?: return
            if (!name.startsWith(NAME) || name == LOADER_NAME || status != Status.SCANNING) return
            stopScan()
            deviceName = name
            setStatus(Status.CONNECTING, name)
            gatt = result.device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE)
        }

        override fun onScanFailed(errorCode: Int) {
            setStatus(Status.IDLE, "Поиск Bluetooth не запустился (код $errorCode)")
        }
    }

    private fun stopScan() {
        main.removeCallbacks(scanTimeout)
        if (adapter?.isEnabled == true) adapter.bluetoothLeScanner?.stopScan(scan)
    }

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            main.post {
                if (newState == BluetoothProfile.STATE_CONNECTED) {
                    gatt.discoverServices()
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    val wasConnected = this@BleTransport.status == Status.CONNECTED
                    close()
                    setStatus(if (wasConnected) Status.LOST else Status.IDLE,
                              if (wasConnected) "" else "Не удалось подключиться (код $status)")
                }
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            main.post { onServices(gatt) }
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            main.post {
                synchronized(lock) { busySince = null }
                setStatus(Status.CONNECTED, deviceName)
                pump()
            }
        }

        override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            main.post {
                synchronized(lock) { busySince = null }
                pump()
            }
        }

        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic,
                                             value: ByteArray) {
            received(value)
        }

        @Deprecated("До Android 13 значение приходит только так")
        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION")
            received(characteristic.value ?: return)
        }
    }

    private fun received(frame: ByteArray) {
        val packet = synchronized(reassembler) { reassembler.feed(frame) } ?: return
        listener.onPacket(packet)
    }

    private fun onServices(gatt: BluetoothGatt) {
        val service = gatt.getService(SERVICE)
        val down = service?.getCharacteristic(CRTP_DOWN)
        crtp = service?.getCharacteristic(CRTP)
        crtpUp = service?.getCharacteristic(CRTP_UP)
        val config = down?.getDescriptor(CLIENT_CONFIG)
        if (crtp == null || crtpUp == null || config == null) {
            close()
            setStatus(Status.IDLE, "Это устройство — не Crazyflie")
            return
        }
        gatt.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)  // меньше задержка команд
        gatt.setCharacteristicNotification(down, true)
        synchronized(lock) { busySince = SystemClock.elapsedRealtime() }
        val value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            gatt.writeDescriptor(config, value)
        } else {
            @Suppress("DEPRECATION")
            config.value = value
            @Suppress("DEPRECATION")
            gatt.writeDescriptor(config)
        }
    }

    private fun pump() {
        val gatt = gatt ?: return
        if (status != Status.CONNECTED) return
        var fromLatest = false
        val frame = synchronized(lock) {
            val since = busySince
            if (since != null && SystemClock.elapsedRealtime() - since < WRITE_TIMEOUT_MS) return
            val next = control.removeFirstOrNull() ?: latest?.also {
                latest = null
                fromLatest = true
            } ?: return
            busySince = SystemClock.elapsedRealtime()
            next
        }
        if (write(gatt, frame)) return
        synchronized(lock) {
            busySince = null
            // Стек занят. Служебный кадр — обратно в начало очереди; setpoint — только если новее не пришло.
            if (!fromLatest) control.addFirst(frame) else if (latest == null) latest = frame
        }
        main.postDelayed({ pump() }, RETRY_MS)
    }

    private fun write(gatt: BluetoothGatt, frame: BleFrame): Boolean {
        val characteristic = (if (frame.segmented) crtpUp else crtp) ?: return false
        val noResponse = characteristic.properties and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0
        val type = if (noResponse) BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                   else BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            return gatt.writeCharacteristic(characteristic, frame.bytes, type) == BluetoothStatusCodes.SUCCESS
        }
        @Suppress("DEPRECATION")
        characteristic.writeType = type
        @Suppress("DEPRECATION")
        characteristic.value = frame.bytes
        @Suppress("DEPRECATION")
        return gatt.writeCharacteristic(characteristic)
    }

    private fun close() {
        stopScan()
        gatt?.let {
            it.disconnect()
            it.close()
        }
        gatt = null
        crtp = null
        crtpUp = null
        synchronized(lock) {
            control.clear()
            latest = null
            busySince = null
        }
        if (status != Status.LOST) status = Status.IDLE
    }

    private fun setStatus(value: Status, detail: String) {
        status = value
        listener.onStatus(value, detail)
    }
}
