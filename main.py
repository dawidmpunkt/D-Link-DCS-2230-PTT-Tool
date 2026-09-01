"""
DCS-2230 Kontroll-Tool
----------------------
PyQt6-GUI ausschließlich für Kamera-Einstellungen und Push-to-Talk.

Der Videostream wird bewusst NICHT eingebettet -- das Tool geht davon aus,
dass Video/Audio separat in einem externen VLC angeschaut wird. Für den
schnellen Wechsel dorthin gibt es einen "RTSP-URL kopieren"-Button.

Start:
    python main.py

Beim ersten Start (keine Config-Datei vorhanden) erscheint automatisch ein
Setup-Dialog für Kamera-Adresse und Zugangsdaten.
"""

import sys
import logging
import threading
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QDialog, QFormLayout, QLineEdit, QSpinBox,
    QDialogButtonBox, QMessageBox, QStatusBar, QComboBox, QSlider, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QKeyEvent

try:
    import keyboard  # fuer globalen PTT-Hotkey
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

from config import CameraConfig, load_config, save_config, CONFIG_FILE
from camera_client import CameraClient, CameraError
from audio_talk import PushToTalkSession, ffmpeg_available
from advanced_settings import AdvancedSettingsDialog
from process_audio import set_process_mute, PYCAW_AVAILABLE
from i18n import tr, set_language
from config_reader import read_io_ir_settings


class SetupDialog(QDialog):
    """Erscheint beim ersten Start bzw. ueber 'Einstellungen', um Kamera-Adresse
    und Zugangsdaten einzugeben und in der Config-Datei zu speichern."""

    def __init__(self, existing: CameraConfig | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("setup_dialog_title"))
        cfg = existing or CameraConfig()

        self.host_edit = QLineEdit(cfg.host)
        self.http_port_edit = QSpinBox()
        self.http_port_edit.setRange(1, 65535)
        self.http_port_edit.setValue(cfg.http_port)
        self.rtsp_port_edit = QSpinBox()
        self.rtsp_port_edit.setRange(1, 65535)
        self.rtsp_port_edit.setValue(cfg.rtsp_port)
        self.user_edit = QLineEdit(cfg.username)
        self.pass_edit = QLineEdit(cfg.password)
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.profile_edit = QSpinBox()
        self.profile_edit.setRange(1, 2)  # Profil 3 hat nachweislich keinen RTSP-Stream
        self.profile_edit.setValue(min(cfg.stream_profile, 2))
        self.hotkey_edit = QLineEdit(cfg.ptt_hotkey)
        self.language_combo = QComboBox()
        self.language_combo.addItems(["en", "de"])
        self.language_combo.setCurrentText(cfg.language)

        form = QFormLayout()
        form.addRow(tr("field_camera_ip"), self.host_edit)
        form.addRow(tr("field_http_port"), self.http_port_edit)
        form.addRow(tr("field_rtsp_port"), self.rtsp_port_edit)
        form.addRow(tr("field_username"), self.user_edit)
        form.addRow(tr("field_password"), self.pass_edit)
        form.addRow(tr("field_rtsp_profile"), self.profile_edit)
        form.addRow(tr("field_ptt_hotkey"), self.hotkey_edit)
        form.addRow(tr("field_language"), self.language_combo)

        hotkey_note = QLabel(tr("hotkey_note"))
        hotkey_note.setStyleSheet("color: gray; font-size: 10px;")
        form.addRow(hotkey_note)

        note = QLabel(tr("password_note", config_file=str(CONFIG_FILE)))
        note.setStyleSheet("color: gray; font-size: 10px;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def result_config(self) -> CameraConfig:
        return CameraConfig(
            host=self.host_edit.text().strip(),
            http_port=self.http_port_edit.value(),
            rtsp_port=self.rtsp_port_edit.value(),
            username=self.user_edit.text().strip(),
            password=self.pass_edit.text(),
            stream_profile=self.profile_edit.value(),
            ptt_hotkey=self.hotkey_edit.text().strip(),
            language=self.language_combo.currentText(),
        )


class MainWindow(QMainWindow):
    # Farben der Verbindungs-Status-Kugel (reines Qt-Stylesheet, keine
    # zusaetzliche Bibliothek noetig). Grau = noch nie verbunden versucht,
    # Gelb = Verbindungsversuch laeuft, Gruen = verbunden, Rot = Fehler.
    _CONN_INDICATOR_STYLE = "border-radius: 7px; background-color: {color};"
    _CONN_COLOR_IDLE = "#9e9e9e"
    _CONN_COLOR_CONNECTING = "#f5c518"
    _CONN_COLOR_OK = "#2ea043"
    _CONN_COLOR_ERROR = "#d32f2f"

    # PTT-Button-Farben: blau im Ruhezustand, rot waehrend aktiv gesprochen
    # wird (zusaetzliches visuelles Feedback -- der Statusleisten-Text allein
    # ist leicht zu uebersehen).
    _TALK_BTN_IDLE_STYLE = (
        "QPushButton { background-color: #2d7dd2; color: white; "
        "font-weight: bold; padding: 8px; border-radius: 4px; }"
        "QPushButton:disabled { background-color: #9e9e9e; color: #e0e0e0; }"
    )
    _TALK_BTN_ACTIVE_STYLE = (
        "QPushButton { background-color: #d32f2f; color: white; "
        "font-weight: bold; padding: 8px; border-radius: 4px; }"
    )

    # Signale, damit der Hotkey-Listener (laeuft im 'keyboard'-Bibliotheks-
    # Thread, nicht im Qt-Haupt-Thread) sicher mit der GUI kommunizieren kann.
    hotkey_pressed = pyqtSignal()
    hotkey_released = pyqtSignal()
    # Signal fuer den Verbindungsaufbau im Hintergrund-Thread (siehe
    # _start_connection_attempt) -- laeuft NICHT automatisch beim Start,
    # sondern erst auf Klick, und blockiert die GUI dabei nicht.
    connection_result = pyqtSignal(bool, str)  # (erfolgreich, nachricht)

    def __init__(self, cfg: CameraConfig):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(tr("window_title", host=cfg.host))
        self.resize(480, 480)

        self.client = CameraClient(cfg)
        # Codec aus der gespeicherten Konfiguration uebernehmen, damit die
        # Stille-Kodierung im Sprech-Kanal von Anfang an passt.
        self.client._speak_codec = cfg.audio_codec
        self._talking = False
        # Debounce fuer Toggle-Modus: 'keyboard' feuert on_press_key mehrfach
        # bei gehaltener Taste (OS-Tastenwiederholung) -- dieses Flag sorgt
        # dafuer, dass nur der allererste Tastendruck einer Halte-Phase als
        # Toggle-Ereignis zaehlt, nicht jede Wiederholung.
        self._hotkey_currently_down = False

        central = self._build_control_panel()
        self.setCentralWidget(central)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(tr("not_connected_yet"))

        self._build_menu()

        # Push-to-Talk-Session (Audio-Capture/Encode/Send laeuft im Hintergrund-Thread)
        self._ptt_session = PushToTalkSession(
            open_stream_callback=self.client.open_speak_stream,
            error_callback=self._on_talk_error,
            codec=cfg.audio_codec,
        )

        # PTT-Hotkey (global, auch wenn das Tool nicht im Vordergrund ist).
        # Signale statt direktem Methodenaufruf, da 'keyboard' in einem
        # eigenen Thread laeuft -- direkte GUI-Aufrufe von dort waeren nicht
        # threadsicher.
        self.hotkey_pressed.connect(self._on_hotkey_pressed_signal)
        self.hotkey_released.connect(self._on_hotkey_released_signal)
        self._register_hotkey()

        # Verbindungsaufbau (client.detect_auth()) laeuft NICHT automatisch,
        # sondern erst auf Klick des "Verbinden"-Buttons -- damit die GUI
        # sofort nutzbar ist (z.B. um erst die IP in den Einstellungen zu
        # korrigieren) und kein Verbindungsversuch im Hintergrund lostickt,
        # den man nicht angefordert hat.
        self.connection_result.connect(self._on_connection_result)

    def _register_hotkey(self):
        if not self.cfg.ptt_hotkey:
            return
        if not self.cfg.ptt_hotkey_global:
            return  # lokaler Modus -- keyPressEvent/keyReleaseEvent des Fensters uebernehmen das
        if not KEYBOARD_AVAILABLE:
            self.status.showMessage(tr("hotkey_missing_keyboard"))
            return
        try:
            keyboard.on_press_key(self.cfg.ptt_hotkey, lambda e: self.hotkey_pressed.emit(), suppress=False)
            keyboard.on_release_key(self.cfg.ptt_hotkey, lambda e: self.hotkey_released.emit(), suppress=False)
        except (ValueError, ImportError) as e:
            self.status.showMessage(tr("hotkey_register_failed", hotkey=self.cfg.ptt_hotkey, error=str(e)))

    def _unregister_hotkey(self):
        if KEYBOARD_AVAILABLE:
            try:
                keyboard.unhook_all()
            except (ValueError, KeyError):
                pass

    def _on_hotkey_pressed_signal(self):
        """Von der globalen 'keyboard'-Bibliothek ausgeloest (eigener Thread,
        daher ueber Signal statt direktem Aufruf)."""
        if self.cfg.ptt_hold_mode:
            self.start_talk()
        else:
            # Toggle-Modus: OS-Tastenwiederholung ignorieren (siehe Kommentar
            # bei self._hotkey_currently_down), nur der erste Druck zaehlt.
            if not self._hotkey_currently_down:
                self._hotkey_currently_down = True
                self._toggle_talk()

    def _on_hotkey_released_signal(self):
        if self.cfg.ptt_hold_mode:
            self.stop_talk()
        else:
            self._hotkey_currently_down = False

    def _toggle_talk(self):
        if self._talking:
            self.stop_talk()
        else:
            self.start_talk()

    def _on_talk_button_pressed(self):
        if self.cfg.ptt_hold_mode:
            self.start_talk()
        # Im Toggle-Modus reagiert der Button erst auf 'clicked' (kompletter
        # Klick), nicht schon auf 'pressed' -- siehe _on_talk_button_released.

    def _on_talk_button_released(self):
        if self.cfg.ptt_hold_mode:
            self.stop_talk()
        else:
            self._toggle_talk()

    def keyPressEvent(self, event: QKeyEvent):
        """Faengt den PTT-Hotkey ab, wenn er auf 'nur dieses Fenster'
        (lokal) statt global eingestellt ist. event.isAutoRepeat() filtert
        OS-Tastenwiederholung heraus, damit Hold-Modus nicht staendig neu
        start_talk() aufruft."""
        if not self.cfg.ptt_hotkey_global and self._matches_hotkey(event) and not event.isAutoRepeat():
            if self.cfg.ptt_hold_mode:
                self.start_talk()
            else:
                self._toggle_talk()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if not self.cfg.ptt_hotkey_global and self._matches_hotkey(event) and not event.isAutoRepeat():
            if self.cfg.ptt_hold_mode:
                self.stop_talk()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _matches_hotkey(self, event: QKeyEvent) -> bool:
        """Vergleicht ein Qt-Tastaturereignis mit dem konfigurierten Hotkey
        (Format der 'keyboard'-Bibliothek, z.B. 'f9', 'ctrl+space').
        ⚠️ Heuristische Uebersetzung zwischen den beiden Formaten -- deckt
        gaengige Faelle ab (Funktionstasten, einfache Buchstaben/Zahlen,
        Modifier-Kombinationen), aber nicht jede von 'keyboard' unterstuetzte
        Tastenbezeichnung."""
        if not self.cfg.ptt_hotkey:
            return False
        parts = [p.strip().lower() for p in self.cfg.ptt_hotkey.split("+")]
        *modifier_parts, key_part = parts

        expected_mods = Qt.KeyboardModifier.NoModifier
        for mod in modifier_parts:
            if mod == "ctrl":
                expected_mods |= Qt.KeyboardModifier.ControlModifier
            elif mod == "shift":
                expected_mods |= Qt.KeyboardModifier.ShiftModifier
            elif mod == "alt":
                expected_mods |= Qt.KeyboardModifier.AltModifier
        if event.modifiers() != expected_mods:
            return False

        key_map = {
            "capslock": Qt.Key.Key_CapsLock, "space": Qt.Key.Key_Space,
            "esc": Qt.Key.Key_Escape, "escape": Qt.Key.Key_Escape,
            "tab": Qt.Key.Key_Tab, "enter": Qt.Key.Key_Return,
            "backspace": Qt.Key.Key_Backspace,
        }
        for i in range(1, 13):
            key_map[f"f{i}"] = getattr(Qt.Key, f"Key_F{i}")

        if key_part in key_map:
            return event.key() == key_map[key_part]
        if len(key_part) == 1:
            return event.key() == ord(key_part.upper())
        return False

    # ------------------------------------------------------------------
    # UI-Aufbau
    # ------------------------------------------------------------------

    def _build_menu(self):
        menu = self.menuBar().addMenu(tr("menu_file"))
        settings_action = menu.addAction(tr("menu_settings"))
        settings_action.triggered.connect(self._open_settings)
        advanced_action = menu.addAction(tr("menu_advanced_settings"))
        advanced_action.triggered.connect(self._open_advanced_settings)
        quit_action = menu.addAction(tr("menu_quit"))
        quit_action.triggered.connect(self.close)

    def _open_advanced_settings(self):
        dialog = AdvancedSettingsDialog(self.client, parent=self)
        dialog.on_codec_changed = self._on_codec_changed
        dialog.audio_codec_combo.setCurrentText(self.cfg.audio_codec)
        dialog.exec()

    def _on_codec_changed(self, codec: str):
        """Haelt PTT-Encoder und gespeicherte Konfiguration mit dem in der
        Kamera eingestellten Codec synchron -- sonst kodiert das Tool in
        einem anderen Format, als die Kamera erwartet."""
        self.cfg.audio_codec = codec
        self._ptt_session.set_codec(codec)
        save_config(self.cfg)
        self.status.showMessage(tr("field_audio_codec") + f" {codec}")

    def _copy_rtsp_url(self):
        url = self.cfg.rtsp_url
        text = f"{url}\n:network-caching={self.network_caching_spin.value()}"
        QApplication.clipboard().setText(text)
        self.status.showMessage(tr("rtsp_url_copied", url=url))

    def _on_network_caching_changed(self, value: int):
        self.cfg.vlc_network_caching = value
        save_config(self.cfg)

    def _set_ir_cut_mode(self, mode: str):
        self._run_camera_command(
            self.client.set_ir_cut_mode, mode,
            success_msg=tr("ir_cut_set", mode=mode)
        )

    def _set_ir_led_mode(self, mode: str):
        self._run_camera_command(
            self.client.set_ir_led_mode, mode,
            success_msg=tr("ir_led_set", mode=mode)
        )

    def _load_ir_quick_settings(self):
        """Laedt IR-Cut-/IR-LED-Modus automatisch nach erfolgreichem
        Verbinden in die Schnellzugriff-Dropdowns im Hauptfenster."""
        try:
            s = read_io_ir_settings(self.client)
        except CameraError:
            return  # Stiller Fehlschlag -- kein Grund, den Verbindungserfolg zu ueberschatten
        if s.ir_cut_mode is not None and s.ir_cut_mode in self.client.IR_CUT_MODES:
            self.ircut_quick_combo.blockSignals(True)
            self.ircut_quick_combo.setCurrentText(s.ir_cut_mode)
            self.ircut_quick_combo.blockSignals(False)
        if s.ir_led_mode is not None and s.ir_led_mode in self.client.IR_LED_MODES:
            self.ir_led_quick_combo.blockSignals(True)
            self.ir_led_quick_combo.setCurrentText(s.ir_led_mode)
            self.ir_led_quick_combo.blockSignals(False)

    def _build_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # Verbindung -- bewusst manuell, kein Auto-Connect beim Start (siehe
        # __init__): so bleibt die GUI sofort nutzbar, auch wenn die Kamera-IP
        # sich geaendert hat und man erst in die Einstellungen muss.
        conn_row = QHBoxLayout()
        self.connect_btn = QPushButton(tr("connect_button"))
        self.connect_btn.clicked.connect(self._start_connection_attempt)
        conn_row.addWidget(self.connect_btn)
        self.conn_indicator = QLabel()
        self.conn_indicator.setFixedSize(14, 14)
        self._set_conn_indicator(self._CONN_COLOR_IDLE)
        conn_row.addWidget(self.conn_indicator)
        conn_row.addStretch()
        layout.addLayout(conn_row)

        # Video-Hinweis + RTSP-Link fuer externen VLC
        layout.addWidget(QLabel(tr("video_audio_header")))
        rtsp_row = QHBoxLayout()
        copy_rtsp_btn = QPushButton(tr("copy_rtsp_button"))
        copy_rtsp_btn.setToolTip(tr("copy_rtsp_tooltip"))
        copy_rtsp_btn.clicked.connect(self._copy_rtsp_url)
        rtsp_row.addWidget(copy_rtsp_btn)
        layout.addLayout(rtsp_row)

        caching_row = QHBoxLayout()
        caching_row.addWidget(QLabel(tr("network_caching_label")))
        self.network_caching_spin = QSpinBox()
        self.network_caching_spin.setRange(0, 60000)
        self.network_caching_spin.setSuffix(" ms")
        self.network_caching_spin.setValue(self.cfg.vlc_network_caching)
        self.network_caching_spin.setToolTip(tr("network_caching_tooltip"))
        self.network_caching_spin.valueChanged.connect(self._on_network_caching_changed)
        caching_row.addWidget(self.network_caching_spin)
        layout.addLayout(caching_row)

        # Bild-Einstellungen
        layout.addWidget(QLabel(tr("image_header")))
        wb_row = QHBoxLayout()
        wb_row.addWidget(QLabel(tr("white_balance_label")))
        self.wb_combo = QComboBox()
        self.wb_combo.addItems(self.client.WHITE_BALANCE_MODES)
        self.wb_combo.currentTextChanged.connect(self.set_white_balance)
        wb_row.addWidget(self.wb_combo)
        layout.addLayout(wb_row)

        # IR-Cut / IR-LED Schnellzugriff (verschoben aus Digital I/O, Werte
        # werden nach erfolgreichem Verbinden automatisch geladen, siehe
        # _on_connection_result)
        ircut_row = QHBoxLayout()
        ircut_row.addWidget(QLabel(tr("ir_cut_label")))
        self.ircut_quick_combo = QComboBox()
        self.ircut_quick_combo.addItems(list(self.client.IR_CUT_MODES.keys()))
        self.ircut_quick_combo.currentTextChanged.connect(self._set_ir_cut_mode)
        ircut_row.addWidget(self.ircut_quick_combo)
        layout.addLayout(ircut_row)

        ir_led_row = QHBoxLayout()
        ir_led_row.addWidget(QLabel(tr("ir_led_label")))
        self.ir_led_quick_combo = QComboBox()
        self.ir_led_quick_combo.addItems(list(self.client.IR_LED_MODES.keys()))
        self.ir_led_quick_combo.currentTextChanged.connect(self._set_ir_led_mode)
        ir_led_row.addWidget(self.ir_led_quick_combo)
        layout.addLayout(ir_led_row)

        # Push-to-Talk
        layout.addWidget(QLabel(tr("talk_header")))
        if not ffmpeg_available():
            layout.addWidget(QLabel(tr("ffmpeg_missing")))
        gain_row = QHBoxLayout()
        gain_row.addWidget(QLabel(tr("mic_gain_label")))
        self.mic_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.mic_gain_slider.setRange(0, 300)  # 0-300% Verstärkung
        self.mic_gain_slider.setValue(100)
        self.mic_gain_slider.valueChanged.connect(self._on_mic_gain_changed)
        gain_row.addWidget(self.mic_gain_slider)
        self.mic_gain_label = QLabel("100%")
        gain_row.addWidget(self.mic_gain_label)
        layout.addLayout(gain_row)

        self.mute_vlc_checkbox = QCheckBox(tr("mute_vlc_checkbox", process=self.cfg.vlc_process_name))
        self.mute_vlc_checkbox.setChecked(self.cfg.ptt_mute_vlc_process)
        self.mute_vlc_checkbox.stateChanged.connect(self._on_mute_vlc_changed)
        if not PYCAW_AVAILABLE:
            self.mute_vlc_checkbox.setEnabled(False)
            self.mute_vlc_checkbox.setText(
                self.mute_vlc_checkbox.text() + tr("pycaw_missing")
            )
        layout.addWidget(self.mute_vlc_checkbox)

        self.hold_mode_checkbox = QCheckBox(tr("hold_mode_checkbox"))
        self.hold_mode_checkbox.setChecked(self.cfg.ptt_hold_mode)
        self.hold_mode_checkbox.stateChanged.connect(self._on_hold_mode_changed)
        layout.addWidget(self.hold_mode_checkbox)

        self.global_hotkey_checkbox = QCheckBox(tr("global_hotkey_checkbox"))
        self.global_hotkey_checkbox.setChecked(self.cfg.ptt_hotkey_global)
        self.global_hotkey_checkbox.stateChanged.connect(self._on_global_hotkey_changed)
        if not KEYBOARD_AVAILABLE:
            self.global_hotkey_checkbox.setEnabled(False)
        layout.addWidget(self.global_hotkey_checkbox)

        self.talk_btn = QPushButton(tr("talk_button"))
        self.talk_btn.setEnabled(ffmpeg_available())
        self.talk_btn.setStyleSheet(self._TALK_BTN_IDLE_STYLE)
        self.talk_btn.pressed.connect(self._on_talk_button_pressed)
        self.talk_btn.released.connect(self._on_talk_button_released)
        layout.addWidget(self.talk_btn)

        layout.addStretch()
        return panel

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------

    def _set_conn_indicator(self, color: str):
        self.conn_indicator.setStyleSheet(
            self._CONN_INDICATOR_STYLE.format(color=color)
        )

    def _start_connection_attempt(self):
        """Startet detect_auth() in einem Hintergrund-Thread, damit die GUI
        waehrend des (bis zu 15s dauernden) Verbindungsversuchs voll bedienbar
        bleibt -- Button wird waehrenddessen deaktiviert, damit nicht mehrfach
        parallel verbunden wird."""
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText(tr("connecting"))
        self.status.showMessage(tr("connecting"))
        self._set_conn_indicator(self._CONN_COLOR_CONNECTING)

        def worker():
            try:
                method = self.client.detect_auth()
                self.connection_result.emit(True, tr("connected", method=method))
            except CameraError as e:
                self.connection_result.emit(False, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_connection_result(self, success: bool, message: str):
        """Laeuft im Qt-Haupt-Thread (via Signal aus dem Hintergrund-Thread
        aufgerufen) -- hier duerfen GUI-Elemente sicher angefasst werden."""
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText(tr("connect_button"))
        if success:
            self._set_conn_indicator(self._CONN_COLOR_OK)
            self.status.showMessage(message)
            self._load_ir_quick_settings()
        else:
            self._set_conn_indicator(self._CONN_COLOR_ERROR)
            self.status.showMessage(tr("connection_failed"))
            QMessageBox.warning(self, tr("connection_error_title"), message)

    # ------------------------------------------------------------------
    # Kamera-Kommandos
    # ------------------------------------------------------------------

    def _run_camera_command(self, fn, *args, success_msg: str = "OK"):
        try:
            fn(*args)
            self.status.showMessage(success_msg)
        except CameraError as e:
            self.status.showMessage(tr("error_status", error=str(e)))
            QMessageBox.warning(self, tr("camera_error_title"), str(e))

    def set_white_balance(self, mode: str):
        self._run_camera_command(
            self.client.set_white_balance, mode,
            success_msg=tr("white_balance_set", mode=mode)
        )

    def _on_mic_gain_changed(self, value: int):
        self.mic_gain_label.setText(f"{value}%")
        self._ptt_session.set_gain(value / 100.0)

    def _on_mute_vlc_changed(self, state: int):
        self.cfg.ptt_mute_vlc_process = bool(state)
        save_config(self.cfg)

    def _on_hold_mode_changed(self, state: int):
        self.cfg.ptt_hold_mode = bool(state)
        save_config(self.cfg)
        # Falls gerade aktiv gesprochen wird, sauber abbrechen -- der
        # Wechsel mitten in einer laufenden Sprech-Session waere sonst
        # inkonsistent (z.B. Taste war gehalten, jetzt auf Toggle umgestellt).
        if self._talking:
            self.stop_talk()
        self._hotkey_currently_down = False

    def _on_global_hotkey_changed(self, state: int):
        self.cfg.ptt_hotkey_global = bool(state)
        save_config(self.cfg)
        # Alte Registrierung entfernen und passend zum neuen Modus neu
        # aufbauen (global -> 'keyboard'-Hook, lokal -> nur Qt-Tastenereignisse
        # dieses Fensters, siehe keyPressEvent/keyReleaseEvent).
        self._unregister_hotkey()
        self._register_hotkey()

    def start_talk(self):
        if self._talking:
            return  # Schutz gegen Tastatur-Wiederholung bei gehaltenem Hotkey
        self._talking = True
        if self.cfg.ptt_mute_vlc_process:
            set_process_mute(self.cfg.vlc_process_name, True)
        try:
            self.client.start_speak()
            self._ptt_session.set_gain(self.mic_gain_slider.value() / 100.0)
            self._ptt_session.start()
            self.talk_btn.setStyleSheet(self._TALK_BTN_ACTIVE_STYLE)
            self.status.showMessage(tr("talking_status"))
        except (CameraError, RuntimeError) as e:
            self._talking = False
            if self.cfg.ptt_mute_vlc_process:
                set_process_mute(self.cfg.vlc_process_name, False)
            QMessageBox.warning(self, tr("talk_failed_title"), str(e))

    def stop_talk(self):
        if not self._talking:
            return
        self._talking = False
        self._ptt_session.stop()
        self.talk_btn.setStyleSheet(self._TALK_BTN_IDLE_STYLE)
        if self.cfg.ptt_mute_vlc_process:
            set_process_mute(self.cfg.vlc_process_name, False)
        try:
            self.client.stop_speak()
            self.status.showMessage(tr("talk_ended"))
        except CameraError as e:
            self.status.showMessage(tr("talk_end_error_status", error=str(e)))

    def _on_talk_error(self, error: Exception):
        """Wird vom PTT-Hintergrund-Thread aufgerufen, falls das Senden der
        Audiodaten fehlschlaegt (z.B. falsches Format von der Kamera abgelehnt)."""
        self.status.showMessage(tr("talk_error_status", error=str(error)))

    # ------------------------------------------------------------------
    # Einstellungen
    # ------------------------------------------------------------------

    def _open_settings(self):
        dialog = SetupDialog(existing=self.cfg, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.cfg = dialog.result_config()
            save_config(self.cfg)
            set_language(self.cfg.language)

            # BUGFIX: self.client wurde bisher nie aktualisiert -- er hielt
            # weiter die ALTE Kamera-Adresse/Zugangsdaten aus dem Konstruktor,
            # die neue IP griff dadurch erst nach einem Neustart des Tools.
            # Neuen Client mit der aktuellen Config erzeugen und die
            # Auth-Erkennung zuruecksetzen, damit "Verbinden" sauber neu
            # gegen die neue Adresse laeuft.
            self.client = CameraClient(self.cfg)
            self.client._speak_codec = self.cfg.audio_codec
            self._ptt_session._open_stream_callback = self.client.open_speak_stream
            self.connect_btn.setText(tr("connect_button"))
            self.status.showMessage(tr("please_reconnect_status"))
            self._set_conn_indicator(self._CONN_COLOR_IDLE)

            QMessageBox.information(
                self, tr("settings_saved_title"),
                tr("settings_saved_reconnect")
            )


def main():
    # Debug-Logging: zeigt jeden Kamera-Request (URL, HTTP-Status, Anfang der
    # Antwort) in der Konsole an.
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    app = QApplication(sys.argv)

    cfg = load_config()
    if cfg is None:
        dialog = SetupDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)
        cfg = dialog.result_config()
        save_config(cfg)

    # Sprache aus der Config anwenden, BEVOR das Hauptfenster gebaut wird --
    # alle UI-Texte werden bei Widget-Erstellung einmalig uebersetzt, ein
    # spaeterer Sprachwechsel wirkt daher erst nach einem Neustart.
    set_language(cfg.language)

    window = MainWindow(cfg)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
