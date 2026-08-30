"""
Leichtgewichtiges Uebersetzungsmodul.

Englisch ist die Standardsprache der Oberflaeche, Deutsch waehlbar in den
Einstellungen. Nutzung:

    from i18n import tr, set_language
    set_language("de")  # einmalig beim Start, aus der Config gelesen
    label = QLabel(tr("digital_io"))

Bewusst simpel gehalten (kein Qt-Linguist/.ts-Workflow) -- ein Dictionary
reicht fuer die ueberschaubare Anzahl an UI-Strings in diesem Tool.
"""

_current_language = "en"


def set_language(lang: str) -> None:
    global _current_language
    _current_language = lang if lang in ("en", "de") else "en"


def get_language() -> str:
    return _current_language


def tr(key: str, **kwargs) -> str:
    """Uebersetzt den gegebenen Schluessel in die aktuell eingestellte
    Sprache. Faellt auf Englisch zurueck, falls die aktuelle Sprache fehlt,
    und auf den Schluessel selbst, falls er komplett unbekannt ist (so faellt
    ein fehlender Eintrag beim Testen sofort auf, statt eine Exception zu
    werfen). kwargs werden per .format() eingesetzt, fuer dynamische Texte
    wie Statusmeldungen mit eingebetteten Werten."""
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(_current_language, entry.get("en", key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


TRANSLATIONS = {
    # --- Fenstertitel / allgemein ---
    "window_title": {"en": "DCS-2230 Control Tool — {host} (settings + PTT only)",
                      "de": "DCS-2230 Kontroll-Tool — {host} (nur Einstellungen + PTT)"},
    "menu_file": {"en": "File", "de": "Datei"},
    "menu_settings": {"en": "Settings…", "de": "Einstellungen…"},
    "menu_advanced_settings": {"en": "Advanced Settings…", "de": "Erweiterte Einstellungen…"},
    "menu_quit": {"en": "Quit", "de": "Beenden"},

    # --- Setup-Dialog ---
    "setup_dialog_title": {"en": "Camera Settings", "de": "Kamera-Einstellungen"},
    "field_camera_ip": {"en": "Camera IP:", "de": "Kamera-IP:"},
    "field_http_port": {"en": "HTTP port:", "de": "HTTP-Port:"},
    "field_rtsp_port": {"en": "RTSP port:", "de": "RTSP-Port:"},
    "field_username": {"en": "Username:", "de": "Benutzername:"},
    "field_password": {"en": "Password:", "de": "Passwort:"},
    "field_rtsp_profile": {"en": "RTSP profile (for external VLC, 1-2):",
                            "de": "RTSP-Profil (für externen VLC, 1-2):"},
    "field_ptt_hotkey": {"en": "PTT hotkey:", "de": "PTT-Hotkey:"},
    "field_language": {"en": "Language:", "de": "Sprache:"},
    "hotkey_note": {
        "en": "Hotkey syntax follows the 'keyboard' library, e.g. 'f9', 'capslock',\n"
              "'ctrl+space'. Leave empty to disable the hotkey. Works globally\n"
              "(even when the tool is not in the foreground).",
        "de": "Hotkey-Syntax nach der 'keyboard'-Bibliothek, z.B. 'f9', 'capslock',\n"
              "'ctrl+space'. Leer lassen, um den Hotkey zu deaktivieren. Wirkt\n"
              "global (auch wenn das Tool nicht im Vordergrund ist).",
    },
    "password_note": {
        "en": "Note: The password is stored locally in plain text\n"
              "({config_file}). Fine for a camera password on your own LAN,\n"
              "but not a secure password store.",
        "de": "Hinweis: Das Passwort wird lokal im Klartext gespeichert\n"
              "({config_file}). Fuer ein Kamera-Passwort im eigenen LAN\n"
              "in Ordnung, aber kein sicherer Passwortspeicher.",
    },
    "settings_saved_title": {"en": "Saved", "de": "Gespeichert"},
    "settings_saved_text": {"en": "Settings saved.", "de": "Einstellungen gespeichert."},

    # --- Hauptfenster: Verbindung ---
    "connect_button": {"en": "🔌 Connect", "de": "🔌 Verbinden"},
    "connecting": {"en": "Connecting…", "de": "Verbinde…"},
    "connected": {"en": "Connected ({method} auth)", "de": "Verbunden ({method}-Auth)"},
    "connection_failed": {"en": "Connection failed", "de": "Verbindung fehlgeschlagen"},
    "connection_error_title": {"en": "Connection error", "de": "Verbindungsfehler"},
    "not_connected_yet": {"en": "Ready. Not connected yet.", "de": "Bereit. Noch nicht verbunden."},
    "settings_saved_reconnect": {
        "en": "Settings saved. Please click 'Connect' to use the new address.\n"
              "(A language change only takes effect after restarting the tool.)",
        "de": "Einstellungen gespeichert. Bitte auf 'Verbinden' klicken,\num die neue Adresse zu verwenden.\n"
              "(Ein Sprachwechsel wirkt erst nach einem Neustart des Tools.)",
    },
    "please_reconnect_status": {"en": "Settings saved. Please reconnect.",
                                 "de": "Einstellungen gespeichert. Bitte neu verbinden."},
    "hotkey_missing_keyboard": {
        "en": "PTT hotkey configured, but the 'keyboard' package is missing "
              "(pip install keyboard) -- hotkey inactive.",
        "de": "PTT-Hotkey konfiguriert, aber 'keyboard'-Paket fehlt "
              "(pip install keyboard) -- Hotkey inaktiv.",
    },
    "hotkey_register_failed": {
        "en": "PTT hotkey '{hotkey}' could not be registered: {error}",
        "de": "PTT-Hotkey '{hotkey}' konnte nicht registriert werden: {error}",
    },

    # --- Video/RTSP ---
    "video_audio_header": {"en": "<b>Video/Audio</b> — runs externally in VLC, not in this tool",
                            "de": "<b>Video/Audio</b> — läuft extern in VLC, nicht in diesem Tool"},
    "copy_rtsp_button": {"en": "📋 Copy RTSP URL", "de": "📋 RTSP-URL kopieren"},
    "copy_rtsp_tooltip": {"en": "Copy to clipboard, then paste into VLC (Ctrl+N)",
                           "de": "In die Zwischenablage kopieren, dann in VLC (Strg+N) einfügen"},
    "rtsp_url_copied": {"en": "RTSP URL copied: {url}", "de": "RTSP-URL kopiert: {url}"},

    # --- Digital I/O ---
    "digital_io_header": {"en": "<b>Digital I/O</b>", "de": "<b>Digital I/O</b>"},
    "output_off": {"en": "Output: OFF", "de": "Ausgang: AUS"},
    "output_on": {"en": "Output: ON", "de": "Ausgang: AN"},
    "output_activated": {"en": "Digital output activated", "de": "Digitalausgang aktiviert"},
    "output_deactivated": {"en": "Digital output deactivated", "de": "Digitalausgang deaktiviert"},
    "input_default": {"en": "Input: — (refresh manually)", "de": "Eingang: — (manuell aktualisieren)"},
    "input_active": {"en": "Input: ACTIVE", "de": "Eingang: AKTIV"},
    "input_inactive": {"en": "Input: inactive", "de": "Eingang: inaktiv"},
    "input_unknown": {"en": "Input: —", "de": "Eingang: —"},
    "refresh_di_tooltip": {"en": "Query DI status now", "de": "DI-Status jetzt abfragen"},
    "buzzer_checkbox": {"en": "Buzzer on DI trigger", "de": "Buzzer bei DI-Auslösung"},
    "volume_label": {"en": "Volume:", "de": "Lautstärke:"},

    # --- Bild ---
    "image_header": {"en": "<b>Image</b>", "de": "<b>Bild</b>"},
    "white_balance_label": {"en": "White balance:", "de": "Weißabgleich:"},
    "white_balance_set": {"en": "White balance: {mode}", "de": "Weißabgleich: {mode}"},

    # --- Sprechen / PTT ---
    "talk_header": {"en": "<b>Talk</b>", "de": "<b>Sprechen</b>"},
    "ffmpeg_missing": {
        "en": "⚠️ ffmpeg not found — talk disabled.\nInstall ffmpeg and make it available in PATH.",
        "de": "⚠️ ffmpeg nicht gefunden — Sprechen deaktiviert.\nffmpeg installieren und im PATH verfügbar machen.",
    },
    "mic_gain_label": {"en": "Microphone gain (PC):", "de": "Mikrofon-Gain (PC):"},
    "mute_vlc_checkbox": {"en": "Mute external VLC ({process}) while talking",
                           "de": "Externes VLC ({process}) beim Sprechen stummschalten"},
    "pycaw_missing": {"en": " (pycaw missing: pip install pycaw)", "de": " (pycaw fehlt: pip install pycaw)"},
    "talk_button": {"en": "🎙 Push-to-Talk", "de": "🎙 Push-to-Talk"},
    "talking_status": {"en": "🎙 Talking…", "de": "🎙 Spreche…"},
    "talk_ended": {"en": "Talk ended", "de": "Sprechen beendet"},
    "talk_failed_title": {"en": "Talk failed", "de": "Sprechen fehlgeschlagen"},
    "talk_error_status": {"en": "Talk error: {error}", "de": "Sprechen-Fehler: {error}"},
    "talk_end_error_status": {"en": "Error ending talk: {error}", "de": "Fehler beim Beenden: {error}"},

    # --- Fehler ---
    "camera_error_title": {"en": "Camera error", "de": "Kamera-Fehler"},
    "error_status": {"en": "Error: {error}", "de": "Fehler: {error}"},
    "error_loading_title": {"en": "Error loading", "de": "Fehler beim Laden"},

    # --- Advanced Settings Dialog ---
    "advanced_settings_title": {"en": "Advanced Settings", "de": "Erweiterte Einstellungen"},
    "close_button": {"en": "Close", "de": "Schließen"},
    "tab_image_quality": {"en": "Image Quality", "de": "Bildqualität"},
    "tab_video_profiles": {"en": "Video Profiles", "de": "Video-Profile"},
    "tab_audio": {"en": "Audio", "de": "Audio"},
    "tab_io_ir": {"en": "Digital I/O & IR", "de": "Digital I/O & IR"},

    "load_current_settings": {"en": "⟳ Load current settings from camera",
                               "de": "⟳ Aktuelle Einstellungen von der Kamera laden"},
    "load_current_profile_settings": {"en": "⟳ Load current settings for selected profile",
                                       "de": "⟳ Aktuelle Einstellungen des gewählten Profils laden"},

    "field_brightness": {"en": "Brightness (0-8):", "de": "Helligkeit (0-8):"},
    "field_contrast": {"en": "Contrast (0-8):", "de": "Kontrast (0-8):"},
    "field_saturation": {"en": "Saturation (0-255):", "de": "Sättigung (0-255):"},
    "field_sharpness": {"en": "Sharpness (0-8):", "de": "Schärfe (0-8):"},
    "field_denoise": {"en": "Denoise (0-255):", "de": "Rauschunterdrückung (0-255):"},
    "field_exposure_mode": {"en": "Exposure mode:", "de": "Belichtungsmodus:"},
    "field_gain": {"en": "Gain (AGC):", "de": "Gain (AGC):"},
    "field_aspect_ratio": {"en": "Aspect ratio:", "de": "Seitenverhältnis:"},
    "apply_image_quality": {"en": "Apply image quality", "de": "Bildqualität übernehmen"},
    "apply_aspect_ratio": {"en": "Apply aspect ratio", "de": "Seitenverhältnis übernehmen"},

    "active_profiles_label": {"en": "Active profiles:", "de": "Aktive Profile:"},
    "apply_button": {"en": "Apply", "de": "Übernehmen"},
    "edit_profile_label": {"en": "Edit profile:", "de": "Profil bearbeiten:"},
    "field_codec": {"en": "Codec:", "de": "Codec:"},
    "field_frame_size": {"en": "Frame size:", "de": "Frame Size:"},
    "field_view_window": {"en": "View window area:", "de": "View Window Area:"},
    "field_max_framerate": {"en": "Max. frame rate:", "de": "Max. Framerate:"},
    "cbr_checkbox": {"en": "Constant bitrate (instead of fixed quality)",
                      "de": "Konstante Bitrate (statt fester Qualität)"},
    "field_bitrate_cbr": {"en": "Bitrate (if CBR):", "de": "Bitrate (bei CBR):"},
    "field_quality_unverified": {"en": "Quality (if fixed quality, ⚠️ unverified):",
                                  "de": "Qualität (bei fester Qualität, ⚠️ unverifiziert):"},
    "field_keyframe_interval": {"en": "Intra frame period (⚠️ range approximate):",
                                 "de": "Intra Frame Period (⚠️ Bereich ungefähr):"},
    "apply_profile_video": {"en": "Apply profile video settings",
                             "de": "Profil-Video-Einstellungen übernehmen"},

    "audio_in_off_checkbox": {"en": "Audio In OFF (mute camera microphone)",
                               "de": "Audio In AUS (Mikrofon der Kamera stummschalten)"},
    "apply_audio_in": {"en": "Apply Audio In", "de": "Audio In übernehmen"},
    "audio_out_off_checkbox": {"en": "Audio Out OFF (mute camera speaker)",
                                "de": "Audio Out AUS (Lautsprecher der Kamera stummschalten)"},
    "apply_audio_out": {"en": "Apply Audio Out", "de": "Audio Out übernehmen"},
    "field_audio_out_volume": {"en": "Audio Out volume (1-10):", "de": "Audio Out Lautstärke (1-10):"},
    "apply_volume": {"en": "Apply volume", "de": "Lautstärke übernehmen"},
    "field_audio_codec": {"en": "Audio codec:", "de": "Audio-Codec:"},
    "apply_codec": {"en": "Apply codec", "de": "Codec übernehmen"},
    "audio_in_gain_note": {
        "en": "Note: 'Audio In Gain' is the PC microphone gain used while\n"
              "talking (push-to-talk) -- see the slider in the main window,\n"
              "not here. The camera itself offers no gain control for its own\n"
              "microphone, only the on/off switch above.",
        "de": "Hinweis: 'Audio In Gain' ist Mikrofon-Verstärkung des PC-Mikrofons\n"
              "beim Sprechen (Push-to-Talk) -- Regler dafür im Hauptfenster,\n"
              "nicht hier. Die Kamera selbst bietet für ihr eigenes Mikrofon\n"
              "keinen Gain-Regler, nur den Ein/Aus-Schalter oben.",
    },

    "digital_input_header": {"en": "<b>Digital Input</b>", "de": "<b>Digitaleingang</b>"},
    "field_polarity": {"en": "Polarity:", "de": "Polarität:"},
    "apply_polarity": {"en": "Apply polarity", "de": "Polarität übernehmen"},
    "ir_cut_header": {"en": "<b>IR Cut Filter (Day/Night)</b>", "de": "<b>IR-Cut-Filter (Tag/Nacht)</b>"},
    "field_mode": {"en": "Mode:", "de": "Modus:"},
    "apply_ir_cut_mode": {"en": "Apply IR cut mode", "de": "IR-Cut-Modus übernehmen"},
    "ir_led_header": {"en": "<b>IR LED Mode</b> (⚠️ value mapping plausible, not live-confirmed)",
                       "de": "<b>IR-LED-Modus</b> (⚠️ Werte-Zuordnung plausibel, nicht live bestätigt)"},
    "apply_ir_led_mode": {"en": "Apply IR LED mode", "de": "IR-LED-Modus übernehmen"},

    "error_title": {"en": "Error", "de": "Fehler"},

    # --- Advanced Settings: generische Bausteine ---
    "load_error_title": {"en": "Error loading", "de": "Fehler beim Laden"},

    # --- Digital I/O (jetzt in Advanced Settings) ---
    "digital_output_header": {"en": "<b>Digital Output</b>", "de": "<b>Digitalausgang</b>"},
    "digital_input_status_header": {"en": "<b>Digital Input Status</b>", "de": "<b>Digitaleingang-Status</b>"},

    # --- IR-Cut / IR-LED Schnellzugriff im Hauptfenster ---
    "ir_cut_label": {"en": "IR cut filter:", "de": "IR-Cut-Filter:"},
    "ir_led_label": {"en": "IR LED:", "de": "IR-LED:"},
    "ir_cut_set": {"en": "IR cut mode: {mode}", "de": "IR-Cut-Modus: {mode}"},
    "ir_led_set": {"en": "IR LED mode: {mode}", "de": "IR-LED-Modus: {mode}"},

    # --- Push-to-Talk Optionen im Hauptfenster ---
    "hold_mode_checkbox": {"en": "Hold to talk (uncheck for click-to-toggle)",
                            "de": "Gedrückt halten zum Sprechen (deaktivieren für Klick-Umschaltung)"},
    "global_hotkey_checkbox": {"en": "Hotkey works globally (uncheck for this window only)",
                                "de": "Hotkey wirkt global (deaktivieren für nur dieses Fenster)"},

    # --- VLC network-caching ---
    "network_caching_label": {"en": "VLC network-caching:", "de": "VLC-Netzwerkpuffer (network-caching):"},
    "network_caching_tooltip": {
        "en": "Value in milliseconds, included when copying the RTSP URL.\n"
              "Paste both lines into VLC's 'Open Network Stream' advanced options.",
        "de": "Wert in Millisekunden, wird beim Kopieren der RTSP-URL mit angehängt.\n"
              "Beide Zeilen in VLCs 'Netzwerk-Stream öffnen'-Erweitert-Optionen einfügen.",
    },
}
