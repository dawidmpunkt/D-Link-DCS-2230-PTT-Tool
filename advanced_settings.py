"""
Erweiterte Einstellungen -- aufklappbares Fenster (QToolBox / Akkordeon-Stil)
fuer Bildqualität, Video-Profile, Audio-Konfiguration und Digital I/O & IR.

Wird ueber einen Button/Menüpunkt im Hauptfenster geöffnet.
"""

import numpy as np
import sounddevice as sd
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QToolBox, QWidget,
    QComboBox, QSpinBox, QCheckBox, QPushButton, QLabel, QMessageBox, QSlider,
)
from PyQt6.QtCore import QTimer, Qt

from camera_client import CameraClient, CameraError
from config_reader import (
    read_image_and_audio_settings, read_io_ir_settings, read_profile_video_settings,
)
from i18n import tr


def play_buzzer_tone(volume_0_to_100: int, frequency_hz: float = 880.0, duration_s: float = 0.3):
    """PC-seitiger Warnton (kein Kamera-Kommando) -- wird abgespielt, wenn der
    Digitaleingang aktiv wird und der Buzzer aktiviert ist. Lautstaerke wird
    per Amplituden-Skalierung realisiert."""
    sr = 44100
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    amplitude = (volume_0_to_100 / 100.0) * 0.5
    tone = (np.sin(2 * np.pi * frequency_hz * t) * amplitude).astype(np.float32)
    sd.play(tone, samplerate=sr)


class AdvancedSettingsDialog(QDialog):
    def __init__(self, client: CameraClient, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle(tr("advanced_settings_title"))
        self.resize(480, 620)

        # Callback, den das Hauptfenster nach dem Erzeugen setzen kann
        self.on_codec_changed = None    # (codec: str) -> None

        self._last_di_state = False  # fuer Flankenerkennung (Buzzer nur bei Übergang inaktiv->aktiv)

        layout = QVBoxLayout(self)
        self.toolbox = QToolBox()
        layout.addWidget(self.toolbox)

        self.toolbox.addItem(self._build_image_page(), tr("tab_image_quality"))
        self.toolbox.addItem(self._build_profile_page(), tr("tab_video_profiles"))
        self.toolbox.addItem(self._build_audio_page(), tr("tab_audio"))
        self.toolbox.addItem(self._build_io_ir_page(), tr("tab_io_ir"))

        close_btn = QPushButton(tr("close_button"))
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    # ------------------------------------------------------------------
    # Seite: Bildqualität
    # ------------------------------------------------------------------

    def _build_image_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        load_row = QHBoxLayout()
        load_btn = QPushButton(tr("load_current_settings"))
        load_btn.clicked.connect(self._load_image_settings)
        self.image_load_check = self._checkmark_label()
        load_row.addWidget(load_btn)
        load_row.addWidget(self.image_load_check)
        layout.addLayout(load_row)

        form = QFormLayout()
        layout.addLayout(form)

        self.brightness_spin = self._spin(0, 8, 4)
        self.contrast_spin = self._spin(0, 8, 4)
        self.saturation_spin = self._spin(0, 255, 128)
        self.sharpness_spin = self._spin(0, 8, 4)
        self.denoise_spin = self._spin(0, 255, 0)

        self.exposure_combo = QComboBox()
        self.exposure_combo.addItems(self.client.EXPOSURE_MODES)

        self.gain_combo = QComboBox()
        self.gain_combo.addItems([f"{v} dB" for v in self.client.GAIN_LEVELS_DB])

        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(self.client.ASPECT_RATIOS)

        form.addRow(tr("field_brightness"), self.brightness_spin)
        form.addRow(tr("field_contrast"), self.contrast_spin)
        form.addRow(tr("field_saturation"), self.saturation_spin)
        form.addRow(tr("field_sharpness"), self.sharpness_spin)
        form.addRow(tr("field_denoise"), self.denoise_spin)
        form.addRow(tr("field_exposure_mode"), self.exposure_combo)
        form.addRow(tr("field_gain"), self.gain_combo)
        form.addRow(tr("field_aspect_ratio"), self.aspect_combo)

        apply_row, apply_check = self._apply_row(
            tr("apply_image_quality"),
            lambda check: self._apply_image_quality(check)
        )
        form.addRow(apply_row)

        aspect_row, aspect_check = self._apply_row(
            tr("apply_aspect_ratio"),
            lambda check: self._apply_aspect_ratio(check)
        )
        form.addRow(aspect_row)

        return page

    def _load_image_settings(self):
        try:
            s = read_image_and_audio_settings(self.client)
        except CameraError as e:
            QMessageBox.warning(self, tr("load_error_title"), str(e))
            return

        if s.brightness is not None:
            self.brightness_spin.setValue(s.brightness)
        if s.contrast is not None:
            self.contrast_spin.setValue(s.contrast)
        if s.saturation is not None:
            self.saturation_spin.setValue(s.saturation)
        if s.sharpness is not None:
            self.sharpness_spin.setValue(s.sharpness)
        if s.denoise is not None:
            self.denoise_spin.setValue(s.denoise)
        if s.exposure_mode is not None and s.exposure_mode in self.client.EXPOSURE_MODES:
            self.exposure_combo.setCurrentText(s.exposure_mode)
        if s.gain_db is not None and s.gain_db in self.client.GAIN_LEVELS_DB:
            self.gain_combo.setCurrentIndex(self.client.GAIN_LEVELS_DB.index(s.gain_db))
        if s.aspect_ratio is not None and s.aspect_ratio in self.client.ASPECT_RATIOS:
            self.aspect_combo.setCurrentText(s.aspect_ratio)

        # Profilanzahl gehoert zur selben Abfrage, wird aber auf der
        # Profile-Seite angezeigt -- dort mit uebernehmen, falls die Seite
        # inzwischen aufgebaut wurde.
        if s.profile_count is not None and hasattr(self, "profile_count_combo"):
            self.profile_count_combo.setCurrentText(str(s.profile_count))

        self._flash_check(self.image_load_check)

    def _apply_image_quality(self, check_label: QLabel):
        gain_db = self.client.GAIN_LEVELS_DB[self.gain_combo.currentIndex()]
        self._run(
            self.client.set_image_quality,
            brightness=self.brightness_spin.value(),
            contrast=self.contrast_spin.value(),
            saturation=self.saturation_spin.value(),
            sharpness=self.sharpness_spin.value(),
            denoise=self.denoise_spin.value(),
            exposure_mode=self.exposure_combo.currentText(),
            gain_db=gain_db,
            check_label=check_label,
        )

    def _apply_aspect_ratio(self, check_label: QLabel):
        self._run(self.client.set_aspect_ratio, self.aspect_combo.currentText(), check_label=check_label)

    # ------------------------------------------------------------------
    # Seite: Video-Profile
    # ------------------------------------------------------------------

    def _build_profile_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        load_row = QHBoxLayout()
        load_btn = QPushButton(tr("load_current_profile_settings"))
        load_btn.clicked.connect(self._load_profile_video_settings)
        self.profile_load_check = self._checkmark_label()
        load_row.addWidget(load_btn)
        load_row.addWidget(self.profile_load_check)
        layout.addLayout(load_row)

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel(tr("active_profiles_label")))
        self.profile_count_combo = QComboBox()
        self.profile_count_combo.addItems(["1", "2", "3"])
        self.profile_count_combo.setCurrentText("3")
        count_apply_btn = QPushButton(tr("apply_button"))
        count_check = self._checkmark_label()
        count_apply_btn.clicked.connect(
            lambda: self._run(
                self.client.set_profile_count, int(self.profile_count_combo.currentText()),
                check_label=count_check,
            )
        )
        count_row.addWidget(self.profile_count_combo)
        count_row.addWidget(count_apply_btn)
        count_row.addWidget(count_check)
        layout.addLayout(count_row)

        # Profilauswahl für die folgenden Einstellungen
        select_row = QHBoxLayout()
        select_row.addWidget(QLabel(tr("edit_profile_label")))
        self.profile_select_combo = QComboBox()
        self.profile_select_combo.addItems(["1", "2", "3"])
        select_row.addWidget(self.profile_select_combo)
        layout.addLayout(select_row)

        form = QFormLayout()

        self.codec_combo = QComboBox()
        self.codec_combo.addItems(self.client.PROFILE_CODECS)
        form.addRow(tr("field_codec"), self.codec_combo)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(self.client.PROFILE_RESOLUTIONS)
        form.addRow(tr("field_frame_size"), self.resolution_combo)

        self.view_window_combo = QComboBox()
        self.view_window_combo.addItems(self.client.PROFILE_RESOLUTIONS)
        form.addRow(tr("field_view_window"), self.view_window_combo)

        self.framerate_combo = QComboBox()
        self.framerate_combo.addItems([str(v) for v in self.client.PROFILE_FRAMERATES])
        form.addRow(tr("field_max_framerate"), self.framerate_combo)

        self.cbr_checkbox = QCheckBox(tr("cbr_checkbox"))
        self.cbr_checkbox.setChecked(True)
        form.addRow(self.cbr_checkbox)

        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(self.client.PROFILE_BITRATES)
        form.addRow(tr("field_bitrate_cbr"), self.bitrate_combo)

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 100)  # ⚠️ Bereich nicht verifiziert
        self.quality_spin.setValue(50)
        form.addRow(tr("field_quality_unverified"), self.quality_spin)

        self.keyframe_spin = QSpinBox()
        self.keyframe_spin.setRange(1, 30)  # Kamera-Default-Bereich, exaktes Min/Max unverifiziert
        self.keyframe_spin.setValue(15)  # 30 wurde per Log bestätigt von der Kamera abgelehnt (NG)
        form.addRow(tr("field_keyframe_interval"), self.keyframe_spin)

        layout.addLayout(form)

        apply_row, apply_check = self._apply_row(
            tr("apply_profile_video"),
            lambda check: self._apply_profile_video(check)
        )
        layout.addLayout(apply_row)

        layout.addStretch()
        return page

    def _load_profile_video_settings(self):
        profile = int(self.profile_select_combo.currentText())
        try:
            result = read_profile_video_settings(self.client, profile)
        except CameraError as e:
            QMessageBox.warning(self, tr("load_error_title"), str(e))
            return

        data = result.profiles.get(profile, {})
        if data.get("codec") in self.client.PROFILE_CODECS:
            self.codec_combo.setCurrentText(data["codec"])
        if data.get("resolution") in self.client.PROFILE_RESOLUTIONS:
            self.resolution_combo.setCurrentText(data["resolution"])
        if data.get("view_window") in self.client.PROFILE_RESOLUTIONS:
            self.view_window_combo.setCurrentText(data["view_window"])
        if data.get("framerate") is not None:
            try:
                fr = int(data["framerate"])
                if fr in self.client.PROFILE_FRAMERATES:
                    self.framerate_combo.setCurrentText(str(fr))
            except ValueError:
                pass
        if data.get("constant_bitrate") is not None:
            self.cbr_checkbox.setChecked(data["constant_bitrate"])
        if data.get("bitrate") in self.client.PROFILE_BITRATES:
            self.bitrate_combo.setCurrentText(data["bitrate"])
        if data.get("keyframe_interval") is not None:
            try:
                self.keyframe_spin.setValue(int(data["keyframe_interval"]))
            except ValueError:
                pass

        self._flash_check(self.profile_load_check)

    def _apply_profile_video(self, check_label: QLabel):
        profile = int(self.profile_select_combo.currentText())
        framerate = int(self.framerate_combo.currentText())
        self._run(
            self.client.set_profile_video,
            profile=profile,
            codec=self.codec_combo.currentText(),
            resolution=self.resolution_combo.currentText(),
            view_window=self.view_window_combo.currentText(),
            max_framerate=framerate,
            constant_bitrate=self.cbr_checkbox.isChecked(),
            bitrate=self.bitrate_combo.currentText(),
            quality=self.quality_spin.value(),
            keyframe_interval=self.keyframe_spin.value(),
            check_label=check_label,
        )

    # ------------------------------------------------------------------
    # Seite: Audio
    # ------------------------------------------------------------------

    def _build_audio_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        load_row = QHBoxLayout()
        load_btn = QPushButton(tr("load_current_settings"))
        load_btn.clicked.connect(self._load_audio_settings)
        self.audio_load_check = self._checkmark_label()
        load_row.addWidget(load_btn)
        load_row.addWidget(self.audio_load_check)
        layout.addLayout(load_row)

        form = QFormLayout()
        layout.addLayout(form)

        self.audio_in_off_checkbox = QCheckBox(tr("audio_in_off_checkbox"))
        form.addRow(self.audio_in_off_checkbox)
        audio_in_row, audio_in_check = self._apply_row(
            tr("apply_audio_in"),
            lambda check: self._run(self.client.set_audio_in_muted, self.audio_in_off_checkbox.isChecked(), check_label=check)
        )
        form.addRow(audio_in_row)

        self.audio_out_off_checkbox = QCheckBox(tr("audio_out_off_checkbox"))
        form.addRow(self.audio_out_off_checkbox)
        audio_out_row, audio_out_check = self._apply_row(
            tr("apply_audio_out"),
            lambda check: self._run(self.client.set_audio_out_muted, self.audio_out_off_checkbox.isChecked(), check_label=check)
        )
        form.addRow(audio_out_row)

        self.audio_out_volume_combo = QComboBox()
        self.audio_out_volume_combo.addItems([str(v) for v in self.client.AUDIO_OUT_VOLUME_LEVELS])
        self.audio_out_volume_combo.setCurrentText("10")
        form.addRow(tr("field_audio_out_volume"), self.audio_out_volume_combo)
        volume_row, volume_check = self._apply_row(
            tr("apply_volume"),
            lambda check: self._run(self.client.set_audio_out_volume, int(self.audio_out_volume_combo.currentText()), check_label=check)
        )
        form.addRow(volume_row)

        self.audio_codec_combo = QComboBox()
        self.audio_codec_combo.addItems(self.client.AUDIO_CODECS)
        self.audio_codec_combo.setCurrentText("G.726")
        form.addRow(tr("field_audio_codec"), self.audio_codec_combo)
        codec_row, self.codec_check = self._apply_row(
            tr("apply_codec"),
            lambda check: self._apply_audio_codec(check)
        )
        form.addRow(codec_row)

        note = QLabel(tr("audio_in_gain_note"))
        note.setStyleSheet("color: gray; font-size: 10px;")
        form.addRow(note)

        return page

    def _apply_audio_codec(self, check_label: QLabel):
        """Setzt NUR den Kamera-seitigen audiotype (Zuhoeren/RTSP-Listen-
        Stream). ENDGUELTIG BESTAETIGT: Der Sprech-Kanal (speakstream.cgi)
        ist fest auf G.726 verdrahtet, unabhaengig von dieser Einstellung."""
        codec = self.audio_codec_combo.currentText()
        try:
            self.client.set_audio_codec(codec)
        except (CameraError, ValueError) as e:
            QMessageBox.warning(self, tr("error_title"), str(e))
            return
        self._flash_check(check_label)

    def _load_audio_settings(self):
        try:
            s = read_image_and_audio_settings(self.client)
        except CameraError as e:
            QMessageBox.warning(self, tr("load_error_title"), str(e))
            return

        if s.audio_in_muted is not None:
            self.audio_in_off_checkbox.setChecked(s.audio_in_muted)
        if s.audio_out_muted is not None:
            self.audio_out_off_checkbox.setChecked(s.audio_out_muted)
        if s.audio_out_volume is not None:
            self.audio_out_volume_combo.setCurrentText(str(s.audio_out_volume))
        if s.audio_codec is not None and s.audio_codec in self.client.AUDIO_CODECS:
            self.audio_codec_combo.setCurrentText(s.audio_codec)

        self._flash_check(self.audio_load_check)

    # ------------------------------------------------------------------
    # Seite: Digital I/O & IR
    # ------------------------------------------------------------------

    def _build_io_ir_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        load_row = QHBoxLayout()
        load_btn = QPushButton(tr("load_current_settings"))
        load_btn.clicked.connect(self._load_io_ir_settings)
        self.io_ir_load_check = self._checkmark_label()
        load_row.addWidget(load_btn)
        load_row.addWidget(self.io_ir_load_check)
        layout.addLayout(load_row)

        # --- Digitalausgang (hierher verschoben aus dem Hauptfenster) ---
        layout.addWidget(QLabel(tr("digital_output_header")))
        self.digital_out_btn = QPushButton(tr("output_off"))
        self.digital_out_btn.setCheckable(True)
        self.digital_out_btn.clicked.connect(self._toggle_digital_output)
        layout.addWidget(self.digital_out_btn)

        # --- Digitaleingang-Status + Buzzer (ebenfalls verschoben) ---
        layout.addWidget(QLabel(tr("digital_input_status_header")))
        di_row = QHBoxLayout()
        self.di_lamp = QLabel("●")
        self.di_lamp.setStyleSheet("color: gray; font-size: 20px;")
        di_row.addWidget(self.di_lamp)
        self.digital_in_label = QLabel(tr("input_default"))
        di_row.addWidget(self.digital_in_label)
        di_refresh_btn = QPushButton("⟳")
        di_refresh_btn.setToolTip(tr("refresh_di_tooltip"))
        di_refresh_btn.setMaximumWidth(30)
        di_refresh_btn.clicked.connect(self._poll_digital_input)
        di_row.addWidget(di_refresh_btn)
        di_row.addStretch()
        layout.addLayout(di_row)

        buzzer_row = QHBoxLayout()
        self.buzzer_checkbox = QCheckBox(tr("buzzer_checkbox"))
        buzzer_row.addWidget(self.buzzer_checkbox)
        layout.addLayout(buzzer_row)
        buzzer_vol_row = QHBoxLayout()
        buzzer_vol_row.addWidget(QLabel(tr("volume_label")))
        self.buzzer_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.buzzer_volume_slider.setRange(0, 100)
        self.buzzer_volume_slider.setValue(50)
        buzzer_vol_row.addWidget(self.buzzer_volume_slider)
        layout.addLayout(buzzer_vol_row)

        # --- DI-Polarität ---
        form = QFormLayout()
        layout.addWidget(QLabel(tr("digital_input_header")))
        self.di_type_combo = QComboBox()
        self.di_type_combo.addItems(["N.O. (Normally Open)", "N.C. (Normally Closed)"])
        form.addRow(tr("field_polarity"), self.di_type_combo)
        di_pol_row, di_pol_check = self._apply_row(
            tr("apply_polarity"),
            lambda check: self._run(
                self.client.set_digital_input_type,
                self.di_type_combo.currentIndex() == 1,  # Index 1 = N.C.
                check_label=check,
            )
        )
        form.addRow(di_pol_row)
        layout.addLayout(form)

        # --- IR-Cut ---
        layout.addWidget(QLabel(tr("ir_cut_header")))
        ircut_form = QFormLayout()
        self.ircut_combo = QComboBox()
        self.ircut_combo.addItems(list(self.client.IR_CUT_MODES.keys()))
        ircut_form.addRow(tr("field_mode"), self.ircut_combo)
        ircut_row, ircut_check = self._apply_row(
            tr("apply_ir_cut_mode"),
            lambda check: self._run(self.client.set_ir_cut_mode, self.ircut_combo.currentText(), check_label=check)
        )
        ircut_form.addRow(ircut_row)
        layout.addLayout(ircut_form)

        # --- IR-LED ---
        layout.addWidget(QLabel(tr("ir_led_header")))
        led_form = QFormLayout()
        self.ir_led_mode_combo = QComboBox()
        self.ir_led_mode_combo.addItems(list(self.client.IR_LED_MODES.keys()))
        led_form.addRow(tr("field_mode"), self.ir_led_mode_combo)
        led_row, led_check = self._apply_row(
            tr("apply_ir_led_mode"),
            lambda check: self._run(self.client.set_ir_led_mode, self.ir_led_mode_combo.currentText(), check_label=check)
        )
        led_form.addRow(led_row)
        layout.addLayout(led_form)

        layout.addStretch()
        return page

    def _toggle_digital_output(self, checked: bool):
        self.digital_out_btn.setText(tr("output_on") if checked else tr("output_off"))
        self._run(
            self.client.set_digital_output, checked,
        )

    def _poll_digital_input(self):
        try:
            status = self.client.get_alarm_status()
        except CameraError:
            self.digital_in_label.setText(tr("input_unknown"))
            self.di_lamp.setStyleSheet("color: gray; font-size: 20px;")
            return

        di_on = status["digital_input_1"]
        self.digital_in_label.setText(tr("input_active") if di_on else tr("input_inactive"))
        self.di_lamp.setStyleSheet(f"color: {'red' if di_on else 'gray'}; font-size: 20px;")

        if di_on and not self._last_di_state and self.buzzer_checkbox.isChecked():
            play_buzzer_tone(self.buzzer_volume_slider.value())
        self._last_di_state = di_on

    def _load_io_ir_settings(self):
        try:
            s = read_io_ir_settings(self.client)
        except CameraError as e:
            QMessageBox.warning(self, tr("load_error_title"), str(e))
            return

        if s.di_normally_closed is not None:
            self.di_type_combo.setCurrentIndex(1 if s.di_normally_closed else 0)
        if s.ir_cut_mode is not None and s.ir_cut_mode in self.client.IR_CUT_MODES:
            self.ircut_combo.setCurrentText(s.ir_cut_mode)
        if s.ir_led_mode is not None and s.ir_led_mode in self.client.IR_LED_MODES:
            self.ir_led_mode_combo.setCurrentText(s.ir_led_mode)

        self._flash_check(self.io_ir_load_check)

    # ------------------------------------------------------------------
    # Helfer
    # ------------------------------------------------------------------

    @staticmethod
    def _spin(minimum: int, maximum: int, default: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(minimum, maximum)
        s.setValue(default)
        return s

    @staticmethod
    def _checkmark_label() -> QLabel:
        lbl = QLabel("✓")
        lbl.setStyleSheet("color: #2e7d32; font-weight: bold; font-size: 16px;")
        lbl.setVisible(False)
        return lbl

    def _apply_row(self, button_text: str, on_click) -> tuple[QHBoxLayout, QLabel]:
        """Baut eine Zeile mit Button + grünem Haken (statt Erfolgs-Popup).
        on_click bekommt den Haken (QLabel) uebergeben, um ihn an _run()
        durchzureichen."""
        row = QHBoxLayout()
        btn = QPushButton(button_text)
        check = self._checkmark_label()
        btn.clicked.connect(lambda: on_click(check))
        row.addWidget(btn)
        row.addWidget(check)
        row.addStretch()
        return row, check

    def _flash_check(self, check_label: QLabel):
        check_label.setVisible(True)
        QTimer.singleShot(2000, lambda: check_label.setVisible(False))

    def _run(self, fn, *args, check_label: QLabel = None, **kwargs):
        try:
            fn(*args, **kwargs)
            if check_label is not None:
                self._flash_check(check_label)
        except (CameraError, ValueError) as e:
            QMessageBox.warning(self, tr("error_title"), str(e))
