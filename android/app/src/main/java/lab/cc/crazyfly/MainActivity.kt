package lab.cc.crazyfly

import android.Manifest
import android.app.Activity
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.hardware.input.InputManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.min
import android.widget.Button as UiButton

/**
 * Экран пилота: DualSense (подключён к телефону по Bluetooth) -> логика полёта -> Crazyflie по Bluetooth LE.
 * Цикл управления — 50 раз в секунду в своём потоке; экран обновляется 10 раз в секунду.
 */
class MainActivity : Activity(), BleTransport.Listener, InputManager.InputDeviceListener {
    companion object {
        private const val TAG = "CrazyFly"
        private const val LOOP_MS = 20L
        private const val UI_MS = 100L
        private const val MAX_DT = 0.1
        private const val PERMISSIONS_REQUEST = 1
        private const val ENABLE_BLUETOOTH_REQUEST = 2
        private const val TRIGGER_KEY_VALUE = 1.0
        private const val HAT_ON = 0.5f
        private val BACKGROUND = Color.rgb(11, 11, 12)
        private val CARD = Color.rgb(36, 36, 40)
        private val TEXT = Color.rgb(232, 232, 232)
        private val MUTED = Color.rgb(140, 140, 150)
        private val ACCENT = Color.rgb(123, 224, 168)
        private val DANGER = Color.rgb(200, 50, 50)
        private val WARN = Color.rgb(240, 190, 80)

        private val KEYS = mapOf(
            KeyEvent.KEYCODE_BUTTON_A to Button.CROSS,
            KeyEvent.KEYCODE_BUTTON_B to Button.CIRCLE,
            KeyEvent.KEYCODE_BUTTON_X to Button.SQUARE,
            KeyEvent.KEYCODE_BUTTON_Y to Button.TRIANGLE,
            KeyEvent.KEYCODE_BUTTON_L1 to Button.L1,
            KeyEvent.KEYCODE_BUTTON_R1 to Button.R1,
            KeyEvent.KEYCODE_BUTTON_START to Button.OPTIONS,
            KeyEvent.KEYCODE_DPAD_UP to Button.UP,
            KeyEvent.KEYCODE_DPAD_DOWN to Button.DOWN,
            KeyEvent.KEYCODE_DPAD_LEFT to Button.LEFT,
            KeyEvent.KEYCODE_DPAD_RIGHT to Button.RIGHT,
        )

        private const val HELP = "Пульт: левый стик ↕ — газ · правый — наклон · L1/R1 — поворот\n" +
            "✕ — взлёт (газ внизу) · □ — посадка · △ — СТОП моторов · ○ — удержание газа\n" +
            "↑ ↓ — сальто вперёд/назад · ← → — бочка · R2/L2 (зажать) — рывок 45° · Options — manual/FPV"

        fun isGamepad(device: InputDevice?): Boolean {
            val sources = device?.sources ?: return false
            return sources and InputDevice.SOURCE_GAMEPAD == InputDevice.SOURCE_GAMEPAD ||
                sources and InputDevice.SOURCE_JOYSTICK == InputDevice.SOURCE_JOYSTICK
        }
    }

    private data class Snapshot(val phase: Phase, val mode: Mode, val sticks: Sticks, val message: String,
                                val holding: Boolean)

    private val pilot = Pilot()
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var ble: BleTransport
    private lateinit var loop: ScheduledExecutorService
    private lateinit var inputManager: InputManager

    @Volatile
    private var link: DroneLink? = null

    @Volatile
    private var pad: PadState? = null  // null — пульта нет

    @Volatile
    private var loopError = ""
    private var padId: Int? = null
    private var padName = ""
    private val keys = HashSet<Button>()
    private val hat = HashSet<Button>()
    private var axes = PadState()
    private var triggerKeys = 0.0 to 0.0  // L2/R2, если пульт шлёт их кнопками, а не осями
    private var bleDetail = ""
    private var lastTick = 0L

    private lateinit var droneText: TextView
    private lateinit var padText: TextView
    private lateinit var phaseText: TextView
    private lateinit var detailText: TextView
    private lateinit var messageText: TextView
    private lateinit var modeButton: UiButton

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        ble = BleTransport(this, this)
        inputManager = getSystemService(InputManager::class.java)
        setContentView(buildLayout())
        loop = Executors.newSingleThreadScheduledExecutor()
        lastTick = SystemClock.elapsedRealtimeNanos()
        // Фиксированная пауза, а не частота: после сна процесса пропущенные тики не догоняются пачкой.
        loop.scheduleWithFixedDelay(::controlTick, 0, LOOP_MS, TimeUnit.MILLISECONDS)
        ui.post(refresh)
    }

    override fun onResume() {
        super.onResume()
        inputManager.registerInputDeviceListener(this, ui)
        findGamepad()
    }

    override fun onPause() {
        super.onPause()
        // Пока экран не наш, нажатия пульта уходят другому приложению — летать вслепую нельзя: пилот сажает дрон.
        inputManager.unregisterInputDeviceListener(this)
        dropGamepad()
    }

    override fun onDestroy() {
        loop.shutdownNow()
        link?.close()
        ui.removeCallbacks(refresh)
        ui.postDelayed({ ble.disconnect() }, UI_MS)  // дать стопу уйти
        super.onDestroy()
    }

    // ---------- цикл управления ----------

    private fun controlTick() {
        try {
            val now = SystemClock.elapsedRealtimeNanos()
            val dt = min((now - lastTick) / 1e9, MAX_DT)
            lastTick = now
            val current = link
            val connected = current != null && ble.status == BleTransport.Status.CONNECTED
            synchronized(pilot) {
                val output = pilot.tick(pad, dt, current?.firmwareRate, connected)
                if (connected) current?.execute(output)
            }
        } catch (error: Exception) {
            // Поток управления не должен умирать молча: иначе команды перестанут уходить без объяснения.
            Log.e(TAG, "Ошибка в цикле управления", error)
            loopError = "Ошибка программы: ${error.message}"
        }
    }

    // ---------- Bluetooth ----------

    override fun onStatus(status: BleTransport.Status, detail: String) {
        bleDetail = detail
        when (status) {
            BleTransport.Status.CONNECTED -> link = DroneLink(ble::send) { SystemClock.elapsedRealtime() }
            BleTransport.Status.LOST, BleTransport.Status.IDLE -> {
                link = null
                synchronized(pilot) { pilot.linkLost() }
            }
            else -> Unit
        }
    }

    override fun onPacket(packet: ByteArray) {
        link?.onPacket(packet)
    }

    private fun connectDrone() {
        val missing = neededPermissions().filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), PERMISSIONS_REQUEST)
            return
        }
        val adapter = getSystemService(BluetoothManager::class.java)?.adapter
        if (adapter != null && !adapter.isEnabled) {
            @Suppress("DEPRECATION")
            startActivityForResult(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE), ENABLE_BLUETOOTH_REQUEST)
            return
        }
        ble.connect()
    }

    private fun neededPermissions(): List<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            listOf(Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.ACCESS_FINE_LOCATION)
        }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != PERMISSIONS_REQUEST) return
        if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
            connectDrone()
        } else {
            synchronized(pilot) { pilot.show("Без разрешения на Bluetooth дрон не найти") }
        }
    }

    @Deprecated("Ответ на системный запрос «включить Bluetooth»")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == ENABLE_BLUETOOTH_REQUEST && resultCode == RESULT_OK) ble.connect()
    }

    // ---------- пульт ----------

    override fun onInputDeviceAdded(deviceId: Int) = findGamepad()

    override fun onInputDeviceChanged(deviceId: Int) = Unit

    override fun onInputDeviceRemoved(deviceId: Int) {
        if (deviceId != padId) return
        dropGamepad()
        findGamepad()
    }

    private fun findGamepad() {
        if (padId != null) return
        val device = inputManager.inputDeviceIds.asSequence().map { InputDevice.getDevice(it) }.firstOrNull(::isGamepad)
            ?: return
        padId = device.id
        padName = device.name
        publish()
    }

    private fun dropGamepad() {
        padId = null
        padName = ""
        keys.clear()
        hat.clear()
        axes = PadState()
        triggerKeys = 0.0 to 0.0
        pad = null
    }

    private fun adopt(device: InputDevice) {
        if (padId == device.id) return
        padId = device.id
        padName = device.name
    }

    override fun dispatchGenericMotionEvent(event: MotionEvent): Boolean {
        val device = event.device
        if (!isGamepad(device) || event.action != MotionEvent.ACTION_MOVE) return super.dispatchGenericMotionEvent(event)
        adopt(device)
        readAxes(event, device)
        publish()
        return true
    }

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val device = event.device
        if (!isGamepad(device)) return super.dispatchKeyEvent(event)
        if (event.action != KeyEvent.ACTION_DOWN && event.action != KeyEvent.ACTION_UP) return true
        val down = event.action == KeyEvent.ACTION_DOWN
        val value = if (down) TRIGGER_KEY_VALUE else 0.0
        when (event.keyCode) {
            KeyEvent.KEYCODE_BUTTON_L2 -> triggerKeys = value to triggerKeys.second
            KeyEvent.KEYCODE_BUTTON_R2 -> triggerKeys = triggerKeys.first to value
            else -> {
                // Остальные кнопки пульта (PS, тачпад…) в систему не выпускаем: «назад» посреди полёта ни к чему.
                val button = KEYS[event.keyCode] ?: return true
                if (down) keys.add(button) else keys.remove(button)
            }
        }
        adopt(device)
        publish()
        return true
    }

    private fun readAxes(event: MotionEvent, device: InputDevice) {
        // У DualSense и большинства пультов правый стик — Z/RZ, курки — LTRIGGER/RTRIGGER; у некоторых — RX/RY.
        val zIsStick = device.getMotionRange(MotionEvent.AXIS_RZ) != null &&
            (device.getMotionRange(MotionEvent.AXIS_LTRIGGER) != null || device.getMotionRange(MotionEvent.AXIS_RX) == null)
        val rightX = if (zIsStick) MotionEvent.AXIS_Z else MotionEvent.AXIS_RX
        val rightY = if (zIsStick) MotionEvent.AXIS_RZ else MotionEvent.AXIS_RY
        axes = PadState(
            leftX = event.getAxisValue(MotionEvent.AXIS_X).toDouble(),
            leftY = event.getAxisValue(MotionEvent.AXIS_Y).toDouble(),
            rightX = event.getAxisValue(rightX).toDouble(),
            rightY = event.getAxisValue(rightY).toDouble(),
            l2 = max(event.getAxisValue(MotionEvent.AXIS_LTRIGGER), event.getAxisValue(MotionEvent.AXIS_BRAKE)).toDouble(),
            r2 = max(event.getAxisValue(MotionEvent.AXIS_RTRIGGER), event.getAxisValue(MotionEvent.AXIS_GAS)).toDouble(),
        )
        val hatX = event.getAxisValue(MotionEvent.AXIS_HAT_X)
        val hatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
        hat.clear()
        if (hatY < -HAT_ON) hat.add(Button.UP)
        if (hatY > HAT_ON) hat.add(Button.DOWN)
        if (hatX < -HAT_ON) hat.add(Button.LEFT)
        if (hatX > HAT_ON) hat.add(Button.RIGHT)
    }

    /** Снимок пульта для потока управления — неизменяемый, без гонок. */
    private fun publish() {
        pad = axes.copy(
            l2 = max(axes.l2, triggerKeys.first),
            r2 = max(axes.r2, triggerKeys.second),
            buttons = (keys + hat).toSet(),
        )
    }

    // ---------- экран ----------

    private val refresh = object : Runnable {
        override fun run() {
            render()
            ui.postDelayed(this, UI_MS)
        }
    }

    private fun render() {
        val status = ble.status
        val connected = status == BleTransport.Status.CONNECTED
        droneText.text = "Дрон: ${status.label}" + if (bleDetail.isNotEmpty()) " — $bleDetail" else ""
        droneText.setTextColor(if (connected) ACCENT else WARN)
        padText.text = if (padName.isNotEmpty()) "Пульт: $padName" else "Пульт не найден — подключи DualSense в настройках Bluetooth телефона"
        padText.setTextColor(if (padName.isNotEmpty()) ACCENT else WARN)
        val now = synchronized(pilot) {
            Snapshot(pilot.state.phase, pilot.mode, pilot.sticks, pilot.shownMessage, pilot.holding)
        }
        phaseText.text = now.phase.label
        phaseText.setTextColor(if (now.phase == Phase.KILLED) DANGER else TEXT)
        val firmware = when (link?.firmwareRate) {
            true -> "скорости (rate)"
            false -> "углы"
            null -> "—"
        }
        val throttle = (now.sticks.throttle + 1) * 50
        detailText.text = "Режим: ${now.mode.label} · прошивка: $firmware · газ: ${"%.0f".format(throttle)}%" +
            if (now.holding) " · удержание" else ""
        val problems = listOfNotNull(now.message, link?.error, loopError).filter { it.isNotEmpty() }
        messageText.text = problems.joinToString("\n")
        modeButton.text = "Режим: ${now.mode.label}"
    }

    private fun buildLayout(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(BACKGROUND)
            setPadding(dp(16), dp(12), dp(16), dp(12))
        }
        root.addView(label("CC Lab · CrazyFly", 20f, TEXT, bold = true))
        droneText = label("", 14f, WARN).also(root::addView)
        padText = label("", 14f, WARN).also(root::addView)
        phaseText = label(Phase.IDLE.label, 34f, TEXT, bold = true).also(root::addView)
        detailText = label("", 14f, MUTED).also(root::addView)
        messageText = label("", 16f, ACCENT).also { it.minLines = 2 }.also(root::addView)

        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        buttons.addView(action("Подключить дрон", CARD) { connectDrone() })
        modeButton = action("Режим: manual", CARD) { synchronized(pilot) { pilot.toggleMode() } }.also(buttons::addView)
        buttons.addView(action("Посадка", CARD) { synchronized(pilot) { pilot.requestLand() } })
        buttons.addView(action("СТОП МОТОРОВ", DANGER) { synchronized(pilot) { pilot.requestKill() } }.also {
            it.layoutParams = weighted(1.6f)
        })
        root.addView(buttons)
        root.addView(label(HELP, 12f, MUTED).also { it.setPadding(0, dp(10), 0, 0) })
        return root
    }

    private fun label(text: String, size: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        this.text = text
        textSize = size
        setTextColor(color)
        if (bold) typeface = Typeface.DEFAULT_BOLD
        setPadding(0, dp(2), 0, dp(2))
    }

    private fun action(text: String, color: Int, onClick: () -> Unit) = UiButton(this).apply {
        this.text = text
        isAllCaps = false
        setTextColor(TEXT)
        setBackgroundColor(color)
        isFocusable = false  // стрелки пульта не должны «бегать» по кнопкам экрана
        setOnClickListener { onClick() }
        layoutParams = weighted(1f)
    }

    private fun weighted(weight: Float) = LinearLayout.LayoutParams(0, dp(56), weight).apply {
        setMargins(dp(4), dp(4), dp(4), dp(4))
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
