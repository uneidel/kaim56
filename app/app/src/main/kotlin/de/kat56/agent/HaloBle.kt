// kAIm56 KatAgent — Android client for the kAIm56 agent platform
// Copyright (C) 2026 Ulrich Neidel
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Bluetooth substrate for the glasses: scans, connects, negotiates the MTU,
// subscribes to notifications and writes packets at the pace of the
// acknowledgements. The messages themselves are built by Halo.kt — this file
// only knows bytes.
//
// CAUTION, untested: everything here can only be checked with real glasses.
// Framing, reassembly and the device side are tested separately (HaloTest.kt,
// tools/halo/test_frame_app.lua); what sits here is the remainder that cannot
// be proven without hardware.
package de.kat56.agent

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.ParcelUuid
import androidx.core.content.ContextCompat
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Connection to a pair of Brilliant glasses.
 *
 * Calls to [write] and [writeString] BLOCK until the glasses have acknowledged
 * — they belong on a background thread, never on the main thread. That is
 * deliberate: the glasses set the pace (they acknowledge every packet), and a
 * blocking call makes that cadence visible instead of hiding it behind a queue.
 */
@SuppressLint("MissingPermission")   // permissions are checked in connect()
class HaloBle(private val ctx: Context) : HaloLink {

    interface Listener {
        /** connected and ready (MTU negotiated, notifications on). */
        fun onReady(isHalo: Boolean) {}
        fun onDisconnected(reason: String) {}
        /** Text from the Lua interpreter (print, error messages). */
        fun onText(text: String) {}
        /** A piece of the recording; [done] = the recording has ended. */
        fun onAudio(pcm: ByteArray, done: Boolean) {}
        /** A piece of a photo; [done] = the image is complete. */
        fun onPhoto(part: ByteArray, done: Boolean) {}
    }

    private var gatt: BluetoothGatt? = null
    private var tx: BluetoothGattCharacteristic? = null
    private var rx: BluetoothGattCharacteristic? = null
    private var listener: Listener? = null

    @Volatile private var negotiatedMtu = 23      // the BLE default until more is negotiated
    @Volatile private var halo = false
    @Volatile private var ready = false

    override val mtu: Int get() = negotiatedMtu
    override val isHalo: Boolean get() = halo

    /** Queues of length 1: one step per write. */
    private val writeDone = ArrayBlockingQueue<Boolean>(1)
    private val acks = ArrayBlockingQueue<Boolean>(1)
    private val connected = ArrayBlockingQueue<Boolean>(1)
    /** Finished photos — [awaitPhoto] picks them up here. */
    private val photos = ArrayBlockingQueue<ByteArray>(1)
    private val writeLock = Any()

    private val audio = Halo.AudioCollector()
    private val photo = Halo.PhotoCollector()

    // ---- Scan and connect ------------------------------------------------

    /** Missing permissions as a list — empty means: good to go. */
    fun missingPermissions(): List<String> {
        val need = if (Build.VERSION.SDK_INT >= 31)
            listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else
            listOf(Manifest.permission.ACCESS_FINE_LOCATION)
        return need.filter {
            ContextCompat.checkSelfPermission(ctx, it) != PackageManager.PERMISSION_GRANTED
        }
    }

    /**
     * Finds the nearest glasses and connects. Blocks until done.
     * Returns: null = connected, otherwise the reason it failed.
     */
    fun connect(timeoutMs: Long = 30000, listener: Listener): String? {
        this.listener = listener
        missingPermissions().let { if (it.isNotEmpty()) return "missing permissions: ${it.joinToString()}" }
        val mgr = ctx.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
            ?: return "no Bluetooth on this device"
        val adapter: BluetoothAdapter = mgr.adapter ?: return "no Bluetooth adapter"
        if (!adapter.isEnabled) return "Bluetooth is off"

        val found = ArrayBlockingQueue<BluetoothDevice>(1)
        val scanner = adapter.bluetoothLeScanner ?: return "scanner not available"
        val cb = object : ScanCallback() {
            override fun onScanResult(type: Int, result: ScanResult) {
                result.device?.let { found.offer(it) }
            }
        }
        // Only devices offering the glasses service — otherwise you catch
        // half the living room.
        val filter = ScanFilter.Builder()
            .setServiceUuid(ParcelUuid(UUID.fromString(Halo.SERVICE))).build()
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        scanner.startScan(listOf(filter), settings, cb)
        val device = try {
            found.poll(timeoutMs, TimeUnit.MILLISECONDS)
        } finally {
            runCatching { scanner.stopScan(cb) }
        } ?: return "no glasses found"

        connected.clear()
        gatt = device.connectGatt(ctx, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
        val ok = connected.poll(timeoutMs, TimeUnit.MILLISECONDS)
        return if (ok == true) null else "the connection did not come up"
    }

    fun disconnect() {
        ready = false
        runCatching { gatt?.disconnect() }
        runCatching { gatt?.close() }
        gatt = null
    }

    // ---- Sending ---------------------------------------------------------

    /**
     * Write one packet and wait for the glasses to acknowledge it. They set the
     * pace: without the wait their receive buffer overflows.
     */
    override fun write(packet: ByteArray) {
        val c = tx ?: throw IllegalStateException("not connected")
        require(ready) { "connection not ready yet" }
        require(packet.size <= Halo.maxPacket(Halo.maxDataLength(negotiatedMtu, halo))) {
            "packet larger than the negotiated MTU allows"
        }
        synchronized(writeLock) {
            writeDone.clear(); acks.clear()
            send(c, packet, withResponse = true)
            check(writeDone.poll(5, TimeUnit.SECONDS) == true) { "the glasses do not confirm the write" }
            val ack = acks.poll(5, TimeUnit.SECONDS)
                ?: throw IllegalStateException("no acknowledgement from the glasses")
            check(ack) { "the glasses report a receive error" }
        }
    }

    /** A line for the Lua interpreter — without 0x01 and without an ack. */
    override fun writeString(text: String) {
        val c = tx ?: throw IllegalStateException("not connected")
        val bytes = text.toByteArray(Charsets.UTF_8)
        require(bytes.size <= Halo.maxStringLength(negotiatedMtu, halo)) { "line too long" }
        synchronized(writeLock) {
            writeDone.clear()
            send(c, bytes, withResponse = true)
            check(writeDone.poll(5, TimeUnit.SECONDS) == true) { "the line was not accepted" }
        }
    }

    @Suppress("DEPRECATION")
    private fun send(c: BluetoothGattCharacteristic, data: ByteArray, withResponse: Boolean) {
        val g = gatt ?: throw IllegalStateException("not connected")
        val type = if (withResponse) BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                   else BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
        if (Build.VERSION.SDK_INT >= 33) {
            g.writeCharacteristic(c, data, type)
        } else {
            c.writeType = type
            c.value = data
            g.writeCharacteristic(c)
        }
    }

    // ---- Receiving -------------------------------------------------------

    private fun onNotify(raw: ByteArray) {
        if (!Halo.isDataFrame(raw)) {
            // Not a data frame -> output of the Lua interpreter.
            listener?.onText(String(raw, Charsets.UTF_8))
            return
        }
        val frame = Halo.frameOf(raw)
        when {
            Halo.isAck(frame) -> acks.offer(!Halo.isAckError(frame))
            frame.isNotEmpty() && (frame[0] == Halo.AUDIO_CHUNK || frame[0] == Halo.AUDIO_FINAL) -> {
                val pcm = audio.feed(frame)
                listener?.onAudio(pcm, audio.complete)
            }
            frame.isNotEmpty() && (frame[0] == Halo.PHOTO_CHUNK || frame[0] == Halo.PHOTO_FINAL) -> {
                val done = photo.feed(frame)
                if (done) {
                    val jpeg = photo.jpeg()
                    photo.reset()
                    photos.offer(jpeg)          // a waiting caller gets it
                    listener?.onPhoto(jpeg, true)
                } else {
                    listener?.onPhoto(frame.copyOfRange(1, frame.size), false)
                }
            }
        }
    }

    /**
     * Waits for the next complete photo. Blocks; null = none arrived in time
     * (camera off, connection gone, the read hangs).
     */
    fun awaitPhoto(timeoutMs: Long = 20000): ByteArray? {
        photos.clear()                       // do not hand back an old capture
        return photos.poll(timeoutMs, TimeUnit.MILLISECONDS)
    }

    /** The recording since the last start, as WAV for /api/stt. */
    fun recordingAsWav(sampleRate: Int = 8000, bitsPerSample: Int = 16): ByteArray =
        Halo.wav(audio.pcm(), sampleRate, bitsPerSample)

    // ---- GATT ------------------------------------------------------------

    private val gattCallback = object : BluetoothGattCallback() {

        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                g.discoverServices()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                ready = false
                connected.offer(false)
                listener?.onDisconnected("disconnected (status $status)")
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val svc = g.getService(UUID.fromString(Halo.SERVICE))
            if (svc == null) { connected.offer(false); return }
            tx = svc.getCharacteristic(UUID.fromString(Halo.CHAR_TX))
            rx = svc.getCharacteristic(UUID.fromString(Halo.CHAR_RX))
            // Only Halo has the audio channel — that identifies the model.
            halo = svc.getCharacteristic(UUID.fromString(Halo.CHAR_AUDIO_TX)) != null
            if (tx == null || rx == null) { connected.offer(false); return }
            g.requestMtu(Halo.MTU_REQUEST)
        }

        override fun onMtuChanged(g: BluetoothGatt, mtuValue: Int, status: Int) {
            negotiatedMtu = mtuValue
            // Only switch notifications on now: before this, something could
            // arrive that no longer fits into a packet.
            val c = rx ?: return
            g.setCharacteristicNotification(c, true)
            val cccd = c.getDescriptor(
                UUID.fromString("00002902-0000-1000-8000-00805f9b34fb"))
            if (cccd == null) { connected.offer(false); return }
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT >= 33) {
                g.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
            } else {
                cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                g.writeDescriptor(cccd)
            }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            ready = status == BluetoothGatt.GATT_SUCCESS
            connected.offer(ready)
            if (ready) listener?.onReady(halo)
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            writeDone.offer(status == BluetoothGatt.GATT_SUCCESS)
        }

        // Android 13+ hands the value along; before that it sits in the characteristic.
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic,
                                             value: ByteArray) = onNotify(value)

        @Suppress("DEPRECATION")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            onNotify(c.value ?: return)
        }
    }
}
