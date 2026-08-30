"""
HTTP-Client fuer die D-Link DCS-2230 Kamerasteuerung.

Deckt die Endpunkte ab, die in der Kommandoreferenz (DCS-2230_Command_Reference.md)
dokumentiert und per Wireshark bestaetigt/aus dem JS extrahiert wurden.

Unverifizierte Endpunkte (📄 in der Referenz) sind entsprechend kommentiert.
"""

import base64
import logging
import queue
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from config import CameraConfig

logger = logging.getLogger("dcs2230.camera_client")


class CameraError(Exception):
    """Wird bei fehlgeschlagenen Kamera-Requests geworfen."""


@dataclass
class SpeakStreamHandle:
    """Verwaltet eine laufende Streaming-POST-Verbindung zu speakstream.cgi.
    write() schreibt einen Audio-Chunk in die Verbindung, close() beendet sie
    sauber und liefert das Endergebnis (Status/Fehler)."""
    _queue: "queue.Queue[Optional[bytes]]"
    _thread: threading.Thread
    _result: dict

    def write(self, chunk: bytes) -> None:
        self._queue.put(chunk)

    def close(self, timeout: float = 3.0) -> dict:
        self._queue.put(None)  # Sentinel -> beendet body_generator()
        self._thread.join(timeout=timeout)
        return dict(self._result)


@dataclass
class SpeakSession:
    """Haelt den Zustand einer laufenden Sprech-Session (Push-to-Talk)."""
    token_time_s: int
    started_at: float
    token: Optional[str] = None  # von getspeaktoken zurueckgegebener echter Token


class CameraClient:
    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self.session = requests.Session()
        # DCS-2230 hat CSRF-Referer-Pruefung aktiv (var.js: g_support_csrf_ref=1).
        # cgi-bin-Endpunkte (z.B. eptzpreset.cgi) scheinen ohne passenden
        # Referer-Header mit HTTP 401 abzulehnen, waehrend vb.htm-Endpunkte
        # das offenbar nicht so streng pruefen. Referer daher immer mitschicken.
        self.session.headers.update({
            "Referer": f"{cfg.http_base_url}/index.htm"
        })
        self._auth = None
        self._speak_session: Optional[SpeakSession] = None
        # Codec fuer den Sprech-Kanal -- bestimmt, wie Stille kodiert wird
        # (siehe open_speak_stream). Wird von set_audio_codec() aktualisiert.
        self._speak_codec: str = "G.726"

    # ---------------------------------------------------------------
    # Verbindungsaufbau / Auth-Erkennung
    # ---------------------------------------------------------------

    def detect_auth(self, timeout: float = 5.0) -> str:
        """Probiert (in dieser Reihenfolge) keine Auth, Basic, Digest.
        Gibt einen String zur Anzeige zurueck ('none' / 'basic' / 'digest').

        WICHTIG (Bugfix): Testet NICHT mehr gegen /index.htm, sondern gegen
        einen echten vb.htm-Befehl -- und prueft den Antwort-TEXT auf die
        Kamera-eigene Ablehnungs-Markierung 'UA ' (steht fuer von der Kamera
        abgelehnte/nicht autorisierte Parameter, ein dritter Status neben
        'OK'/'NG', siehe index.js: txt.indexOf("UA getdlinkalarmstatus")).

        WEITERER BUGFIX: Ein reiner LESE-Probe (getdlinkalarmstatus) reicht
        nicht aus. Beobachtet: Mit deaktivierter/laxer Kamera-Konfiguration
        liess sich der Lese-Probe auch mit 'none' erfolgreich abfragen,
        waehrend echte SCHREIB-Befehle (z.B. awb=..., supporttwowayaudio)
        trotzdem konsequent mit 'UA' abgelehnt wurden -- die Kamera scheint
        Lese- und Schreibzugriff unterschiedlich streng zu pruefen. Daher
        wird jetzt zusaetzlich ein echter (harmloser) Schreibbefehl
        gegengetestet, bevor eine Methode als erfolgreich gilt."""
        candidates = [
            ("none", None),
            ("basic", HTTPBasicAuth(self.cfg.username, self.cfg.password)),
            ("digest", HTTPDigestAuth(self.cfg.username, self.cfg.password)),
        ]
        read_probe_url = f"{self.cfg.http_base_url}/vb.htm?language=ie&getdlinkalarmstatus"
        # Harmloser Schreibbefehl: fragt den Autofokus-Busy-Status ab -- nein,
        # das waere wieder nur Lesen. 'awb=Auto' ist ein echter State-Change,
        # aber unkritisch (Standard-Weissabgleich-Modus).
        write_probe_url = f"{self.cfg.http_base_url}/vb.htm?language=ie&awb=Auto"
        errors = []
        for name, auth in candidates:
            try:
                r_read = self.session.get(read_probe_url, auth=auth, timeout=timeout)
                r_write = self.session.get(write_probe_url, auth=auth, timeout=timeout)
            except requests.RequestException as e:
                errors.append(f"{name}: Verbindungsfehler: {e}")
                logger.debug("Auth-Test '%s' fehlgeschlagen: %s", name, e)
                continue
            logger.debug("Auth-Test '%s' (Lesen): HTTP %s, Body: %r", name, r_read.status_code, r_read.text[:100])
            logger.debug("Auth-Test '%s' (Schreiben): HTTP %s, Body: %r", name, r_write.status_code, r_write.text[:100])
            read_ok = r_read.status_code == 200 and "UA " not in r_read.text
            write_ok = r_write.status_code == 200 and "UA " not in r_write.text
            if read_ok and write_ok:
                self._auth = auth
                self.session.auth = auth
                logger.debug("Auth-Methode erkannt: %s (Lesen UND Schreiben bestaetigt)", name)
                return name
            if not write_ok:
                errors.append(
                    f"{name}: Lesen {'OK' if read_ok else 'fehlgeschlagen'}, "
                    f"Schreiben abgelehnt (UA): {r_write.text[:80]!r}"
                )
            else:
                errors.append(f"{name}: HTTP {r_read.status_code}/{r_write.status_code}")
        raise CameraError(
            "Keine Auth-Methode mit vollen Schreibrechten erfolgreich. Details: " + "; ".join(errors)
        )

    # ---------------------------------------------------------------
    # Interner Helfer
    # ---------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None, timeout: float = 5.0):
        url = f"{self.cfg.http_base_url}{path}"
        logger.debug("GET %s params=%s", url, params)
        try:
            r = self.session.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            logger.debug("  -> Verbindungsfehler: %s", e)
            raise CameraError(f"Request an {path} fehlgeschlagen: {e}") from e
        logger.debug("  -> HTTP %s, %d Bytes, Body-Anfang: %r",
                     r.status_code, len(r.content), r.text[:150])
        if r.status_code != 200:
            raise CameraError(f"{path} -> HTTP {r.status_code}")
        self._check_ng_response(r.text, context=path)
        return r

    def _get_flag(self, path: str, *flags: str, timeout: float = 5.0):
        """Fuer Endpunkte, die parameterlose 'Flags' ohne '=' erwarten,
        z.B. '/vb.htm?supporttwowayaudio' statt '/vb.htm?supporttwowayaudio='.
        requests' params-Dict entfernt None-Werte komplett aus der URL (das war
        der Bug hinter dem HTTP-400-Fehler beim Sprechen) -- daher hier die
        Query-String manuell bauen statt ueber das params-Dict zu gehen."""
        url = f"{self.cfg.http_base_url}{path}?{'&'.join(flags)}"
        logger.debug("GET %s", url)
        try:
            r = self.session.get(url, timeout=timeout)
        except requests.RequestException as e:
            logger.debug("  -> Verbindungsfehler: %s", e)
            raise CameraError(f"Request an {path} fehlgeschlagen: {e}") from e
        logger.debug("  -> HTTP %s, %d Bytes, Body-Anfang: %r",
                     r.status_code, len(r.content), r.text[:150])
        if r.status_code != 200:
            raise CameraError(f"{path}?{flags[0]} -> HTTP {r.status_code}")
        self._check_ng_response(r.text, context="&".join(flags) if flags else path)
        return r

    @staticmethod
    def _check_ng_response(text: str, context: str) -> None:
        """Parst die Kamera-Antwort zeilenweise (Format: 'OK <param>[=wert]'
        bzw. 'NG <param>' bzw. 'UA <param>' pro gesendetem Parameter) und wirft
        nur dann einen Fehler, wenn WIRKLICH etwas fehlgeschlagen ist --
        dabei werden erfolgreiche und fehlgeschlagene Felder GETRENNT erfasst.

        BUGFIX: Die alte Version brach bei JEDEM 'NG'/'UA' irgendwo im Text
        ab, auch wenn z.B. 6 von 7 gesendeten Feldern erfolgreich uebernommen
        wurden (siehe Log: profile2format/resolution/view/rate/qmode/bps alle
        OK, nur profile2keyframeinterval NG) -- das liess eine groesstenteils
        erfolgreiche Mehrfeld-Anfrage komplett wie einen Fehlschlag aussehen."""
        failed = []
        for marker, label in (("NG ", "abgelehnt"), ("UA ", "nicht autorisiert")):
            for m in re.finditer(re.escape(marker) + r"([a-zA-Z0-9_]+)", text):
                failed.append(f"{m.group(1)} ({label})")
        if failed:
            raise CameraError(
                f"Kamera hat folgende Parameter abgelehnt ({context}): "
                + ", ".join(failed)
                + (" -- alle anderen gesendeten Parameter wurden übernommen."
                   if "OK " in text else "")
            )

    # ---------------------------------------------------------------
    # Digitaler Ausgang ✅ (per Wireshark bestaetigt)
    # ---------------------------------------------------------------

    def set_digital_output(self, on: bool) -> None:
        state = "1" if on else "0"
        self._get("/vb.htm", params={"language": "ie", "giooutalwayson": f"1:{state}"})

    # ---------------------------------------------------------------
    # Bild-Einstellungen ✅ (aus setup_image.htm extrahiert)
    # ---------------------------------------------------------------

    WHITE_BALANCE_MODES = ("Auto", "Outdoor", "Indoor", "Fluorescent", "Push Hold")

    def set_white_balance(self, mode: str) -> None:
        if mode not in self.WHITE_BALANCE_MODES:
            raise ValueError(f"Ungueltiger Weissabgleich-Modus: {mode}")
        self._get("/vb.htm", params={"language": "ie", "awb": mode})

    # ---------------------------------------------------------------
    # Fokus 📄 -- nur Autofokus (einmalig ausloesen), kein manuelles
    # Nah/Fern-Fokussieren gefunden (das existiert im JS nur fuer Visca-
    # Kameras, bei der DCS-2230 deaktiviert -- passt zur Festbrennweiten-Optik).
    # ---------------------------------------------------------------

    def trigger_autofocus(self) -> None:
        self._get("/cgi-bin/lencontrol.cgi", params={"autofocus": 1})

    # ---------------------------------------------------------------
    # IR-Licht 📄 (aus index.js extrahiert)
    # ---------------------------------------------------------------

    def set_ir_light(self, on: bool) -> None:
        action = "on" if on else "off"
        self._get("/cgi-bin/light_ctrl.cgi", params={"action": action})

    def set_ir_light_brightness(self, level_0_to_100: int) -> None:
        level = max(0, min(100, level_0_to_100))
        self._get("/cgi-bin/light_ctrl.cgi", params={"action": "on", "active": level})

    # ---------------------------------------------------------------
    # Alarm-/DI-/DO-Status ✅ (Bitmaske aus index.js SendOK() extrahiert)
    # ---------------------------------------------------------------

    def get_alarm_status(self) -> dict:
        """Fragt den Status von Digitaleingang, Bewegungserkennung, Aufnahme
        und Digitalausgang ab. Rueckgabe als dict mit bool-Werten.

        Format der Kamera-Antwort: 'OK getdlinkalarmstatus=XXXX' (4-stelliger
        Hex-Code), als Bitmaske interpretiert (siehe index.js, SendOK()):
        Bit 0x01 = DI1, 0x02 = DI2, 0x04 = Bewegung, 0x08 = Aufnahme,
        0x10 = DO (nutzergesteuert), 0x20 = DO (event-getriggert).
        """
        r = self._get_flag("/vb.htm", "language=ie", "getdlinkalarmstatus")
        text = r.text
        idx = text.find("OK getdlinkalarmstatus=")
        if idx < 0:
            raise CameraError(f"Unerwartetes Antwortformat: {text!r}")
        code_str = text[idx + len("OK getdlinkalarmstatus="):idx + len("OK getdlinkalarmstatus=") + 4]
        try:
            code = int(code_str, 16)
        except ValueError as e:
            raise CameraError(f"Konnte Statuscode nicht parsen: {code_str!r}") from e
        return {
            "digital_input_1": bool(code & 0x01),
            "digital_input_2": bool(code & 0x02),
            "motion_detected": bool(code & 0x04),
            "recording": bool(code & 0x08),
            "digital_output_user": bool(code & 0x10),
            "digital_output_event": bool(code & 0x20),
        }

    # ---------------------------------------------------------------
    # Generischer Config-Setter -- mehrere Parameter in einem einzigen
    # Request, genau wie es die "Speichern"-Buttons der Kamera selbst tun
    # (c_iniUrl + mehrere &key=value-Paare in einem GET statt vieler
    # Einzelrequests).
    # ---------------------------------------------------------------

    def _set_config(self, **params) -> None:
        pairs = [f"{k}={v}" for k, v in params.items() if v is not None]
        if not pairs:
            return
        self._get_flag("/vb.htm", "language=ie", *pairs)

    # ---------------------------------------------------------------
    # Erweiterte Bildqualität ✅ (aus setup_image.htm extrahiert,
    # exakte Wertebereiche direkt aus den Ctrl_SelectNum-Definitionen)
    # ---------------------------------------------------------------

    EXPOSURE_MODES = ("Auto", "Indoor", "Outdoor", "Night", "Moving",
                       "Low_noise", "Customize1", "Customize2", "Customize3")
    GAIN_LEVELS_DB = (0, 3, 6, 9, 12, 18, 21, 24)  # AGC-Werte in dB
    ASPECT_RATIOS = ("4:3", "16:9")

    def set_image_quality(
        self,
        brightness: Optional[int] = None,    # 0-8, Default 4
        contrast: Optional[int] = None,      # 0-8, Default 4
        saturation: Optional[int] = None,    # 0-255, Default 128
        sharpness: Optional[int] = None,     # 0-8, Default 4
        denoise: Optional[int] = None,       # 0-255, Default 0
        exposure_mode: Optional[str] = None,  # siehe EXPOSURE_MODES
        gain_db: Optional[int] = None,        # siehe GAIN_LEVELS_DB
    ) -> None:
        if exposure_mode is not None and exposure_mode not in self.EXPOSURE_MODES:
            raise ValueError(f"Ungueltiger Belichtungsmodus: {exposure_mode}")
        if gain_db is not None and gain_db not in self.GAIN_LEVELS_DB:
            raise ValueError(f"Ungueltiger Gain-Wert: {gain_db}")
        self._set_config(
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            sharpness=sharpness,
            denoise=denoise,
            exposuretime=exposure_mode,  # Parametername weicht bewusst vom Feldnamen ab
            agc=gain_db,                  # dito -- "maxgain"-Feld sendet als "agc="
        )

    def set_aspect_ratio(self, ratio: str) -> None:
        if ratio not in self.ASPECT_RATIOS:
            raise ValueError(f"Ungueltiges Seitenverhaeltnis: {ratio}")
        self._set_config(aspectratio=ratio)

    # ---------------------------------------------------------------
    # Video-Profile ✅ (aus setup_audio_video.htm extrahiert)
    # ---------------------------------------------------------------

    PROFILE_CODECS = ("JPEG", "MPEG4", "H.264")
    PROFILE_FRAMERATES = (25, 15, 7, 4, 1)
    PROFILE_BITRATES = ("8M", "6M", "4M", "2M", "1M", "512K", "256K", "200K", "128K", "64K")
    PROFILE_RESOLUTIONS = ("1920x1080", "1280x720", "800x450", "640x360",
                            "480x270", "320x176", "176x144")

    def set_profile_count(self, count: int) -> None:
        """Anzahl aktiver Profile (1-3)."""
        if count not in (1, 2, 3):
            raise ValueError("Profilanzahl muss 1, 2 oder 3 sein.")
        self._set_config(profilenumber=count)

    def set_profile_video(
        self,
        profile: int,
        codec: Optional[str] = None,             # siehe PROFILE_CODECS
        resolution: Optional[str] = None,         # siehe PROFILE_RESOLUTIONS
        view_window: Optional[str] = None,        # "View Window Area", gleiche Werteliste wie resolution
        max_framerate: Optional[int] = None,      # siehe PROFILE_FRAMERATES
        constant_bitrate: bool = True,            # True=CBR (qmode=0), False=Fixed Quality (qmode=1)
        bitrate: Optional[str] = None,            # siehe PROFILE_BITRATES, nur bei constant_bitrate=True
        quality: Optional[int] = None,            # nur bei constant_bitrate=False -- Wertebereich NICHT
                                                   # verifiziert (Template in setup_audio_video.htm nicht
                                                   # aufgeloest)
        keyframe_interval: Optional[int] = None,  # "Intra Frame Period", Kamera-Default-Bereich 1-30,
                                                   # exaktes Min/Max nicht aufgeloest (Templates)
    ) -> None:
        if profile not in (1, 2, 3):
            raise ValueError("Profil muss 1, 2 oder 3 sein.")
        if codec is not None and codec not in self.PROFILE_CODECS:
            raise ValueError(f"Ungueltiger Codec: {codec}")
        if max_framerate is not None and max_framerate not in self.PROFILE_FRAMERATES:
            raise ValueError(f"Ungueltige Framerate: {max_framerate}")
        if bitrate is not None and bitrate not in self.PROFILE_BITRATES:
            raise ValueError(f"Ungueltige Bitrate: {bitrate}")

        p = profile
        self._set_config(**{
            f"profile{p}format": codec,
            f"profile{p}resolution": resolution,
            f"profile{p}view": view_window,
            f"profile{p}rate": max_framerate,
            f"profile{p}qmode": (0 if constant_bitrate else 1),
            f"profile{p}bps": bitrate if constant_bitrate else None,
            f"profile{p}quality": quality if not constant_bitrate else None,
            f"profile{p}keyframeinterval": keyframe_interval,
        })

    # ---------------------------------------------------------------
    # Audio-Einstellungen ✅ (aus setup_audio_video.htm extrahiert)
    # Gelten global fuer alle Profile, nicht pro Profil.
    # WICHTIG: Die Semantik ist invertiert -- "enable"-Parameter=1 bedeutet
    # AUS/gemutet, nicht AN! (Bestaetigt im urspruenglichen Code-Kommentar:
    # "audioinenable = 1 -> 'audio in off' checkbox is selected, means off audio")
    # Kein separater Audio-In-Gain-Parameter gefunden (nur Mute-Schalter) --
    # anders als im offiziellen Handbuch beschrieben ("audio in gain level"),
    # das bezog sich vermutlich auf ein anderes Kameramodell/-Firmware.
    # ---------------------------------------------------------------

    AUDIO_OUT_VOLUME_LEVELS = tuple(range(1, 11))  # 1-10
    AUDIO_CODECS = ("G.726",)

    def set_audio_codec(self, codec: str) -> None:
        """Schaltet den Audio-Codec um (gilt fuer Ein- UND Ausgabe).
        Parameter 'audiotype=' aus setup_audio_video.htm extrahiert.

        WICHTIG fuer Push-to-Talk: Der hier eingestellte Codec muss zu dem
        passen, was audio_talk.py per ffmpeg kodiert -- sonst kommt am
        Kameralautsprecher nur Rauschen oder gar nichts an."""
        if codec not in self.AUDIO_CODECS:
            raise ValueError(f"Ungueltiger Audio-Codec: {codec}")
        self._set_config(audiotype=codec)
        self._speak_codec = codec

    def set_audio_in_muted(self, muted: bool) -> None:
        self._set_config(audioinenable=(1 if muted else 0))

    def set_audio_out_muted(self, muted: bool) -> None:
        self._set_config(audiooutenable=(1 if muted else 0))

    def set_audio_out_volume(self, level_1_to_10: int) -> None:
        if level_1_to_10 not in self.AUDIO_OUT_VOLUME_LEVELS:
            raise ValueError("Lautstärke muss zwischen 1 und 10 liegen.")
        self._set_config(audiooutvolume=level_1_to_10)

    # ---------------------------------------------------------------
    # IR-Cut-Filter (Tag/Nacht) und IR-LED ✅ (aus setup_icr.htm extrahiert)
    # Bestaetigt: fuer diese Kamera (kein Visca/Zbc) gilt weiterhin die
    # normale /vb.htm-Basis-URL -- der in setup_icr.htm definierte
    # viscaicr.cgi/zbcicr.cgi-Override greift wegen g_isSupportVisca=0 und
    # g_supportZbc=0 bei der DCS-2230 NICHT.
    # ---------------------------------------------------------------

    IR_CUT_MODES = {"automatic": 0, "day": 1, "night": 2, "schedule": 3}
    IR_LED_MODES = {"off": 0, "on": 1, "automatic": 2, "schedule": 3}  # ⚠️ Werte-
    # Zuordnung aus Reihenfolge der Radio-Optionen im Code abgeleitet, exakte
    # Beschriftung ("Automatic" vs "Power Sync") variiert je nach Kamera-Variante
    # im Quelltext -- Semantik plausibel, aber nicht live bestaetigt.

    def set_ir_cut_mode(self, mode: str) -> None:
        if mode not in self.IR_CUT_MODES:
            raise ValueError(f"Ungueltiger IR-Cut-Modus: {mode}")
        self._set_config(dncontrolmode=self.IR_CUT_MODES[mode])

    def set_ir_led_mode(self, mode: str) -> None:
        if mode not in self.IR_LED_MODES:
            raise ValueError(f"Ungueltiger IR-LED-Modus: {mode}")
        self._set_config(irledmode=self.IR_LED_MODES[mode])

    def set_ir_led_brightness_level(self, level: int) -> None:
        """Unterscheidet sich von set_ir_light_brightness() (light_ctrl.cgi,
        Live-View-Schnellzugriff) -- dies ist der persistente Setup-Wert
        (irledbrightness=), vermutlich eine Stufenskala statt 0-100, exakte
        Werteliste nicht aufgeloest (Template in setup_icr.htm)."""
        self._set_config(irledbrightness=level)

    # ---------------------------------------------------------------
    # Digitaleingang-Polarität ✅ (aus setup_digital_io.htm extrahiert)
    # ---------------------------------------------------------------

    def set_digital_input_type(self, normally_closed: bool) -> None:
        """False = N.O. (Normally Open), True = N.C. (Normally Closed).
        Bei nur einem Digitaleingang (DCS-2230) wird der Parameter ohne
        Index gesendet ('setgiointype=', nicht 'setgiointype=1:...')."""
        self._set_config(setgiointype=(1 if normally_closed else 0))

    # ---------------------------------------------------------------
    # Rohe Setup-Seite abrufen (fuer config_reader.py) -- liefert den
    # aktuellen HTML/JS-Quelltext, in dem die Kamera die tatsaechlich
    # aktiven Werte bereits eingebettet hat.
    # ---------------------------------------------------------------

    def fetch_page(self, path: str) -> str:
        """Ruft eine rohe Setup-/Status-Seite ab (z.B. fuer config_reader.py).
        Nutzt bewusst KEINEN _get()/NG-Check -- dieser ist fuer /vb.htm-
        Konfigurations-Requests gedacht, nicht fuer beliebige .htm-Seiten,
        die zufaellig den Text 'NG ' irgendwo im Inhalt/Hilfetext enthalten
        koennten (falscher Alarm waere sonst moeglich).

        DEBUG-HILFE: Speichert die abgerufene Seite zusaetzlich lokal als
        Datei (debug_<name>.htm im Arbeitsverzeichnis) -- damit laesst sich
        der volle Seiteninhalt fuer die Fehlersuche direkt aus dem
        Projektordner hochladen, ohne Umweg ueber den Browser (das Debug-Log
        kuerzt lange Inhalte, das hier nicht)."""
        url = f"{self.cfg.http_base_url}{path if path.startswith('/') else '/' + path}"
        logger.debug("GET %s (fetch_page)", url)
        try:
            r = self.session.get(url, timeout=8.0)
        except requests.RequestException as e:
            logger.debug("  -> Verbindungsfehler: %s", e)
            raise CameraError(f"Request an {path} fehlgeschlagen: {e}") from e
        logger.debug("  -> HTTP %s, %d Bytes, Body-Anfang: %r",
                     r.status_code, len(r.content), r.text[:200])
        if r.status_code != 200:
            raise CameraError(f"{path} -> HTTP {r.status_code}")

        try:
            debug_name = "debug_" + path.strip("/").replace("/", "_")
            with open(debug_name, "w", encoding="utf-8", errors="replace") as f:
                f.write(r.text)
            logger.debug("  -> Vollstaendig gespeichert nach: %s", debug_name)
        except OSError as e:
            logger.debug("  -> Debug-Datei konnte nicht geschrieben werden: %s", e)

        return r.text

    # ---------------------------------------------------------------
    # Snapshot ✅
    # ---------------------------------------------------------------

    def snapshot_url(self, profile_id: int = 1) -> str:
        """Gibt die URL fuer einen Snapshot zurueck (Cache-Buster wird automatisch
        angehaengt, damit nicht jedes Mal dasselbe Bild aus einem Proxy-/Browser-
        Cache kommt)."""
        return f"{self.cfg.http_base_url}/dms?nowprofileid={profile_id}&{time.time()}"

    def get_snapshot_bytes(self, profile_id: int = 1) -> bytes:
        r = self._get("/dms", params={"nowprofileid": profile_id, "_": time.time()})
        return r.content

    # ---------------------------------------------------------------
    # Sprechen (Push-to-Talk) ✅ Endpunkt-Ablauf bestaetigt,
    # ⚠️ genaues POST-Format (Content-Type/Body) NICHT verifiziert
    # ---------------------------------------------------------------

    def start_speak(self, token_time_s: int = 2) -> None:
        """Startet eine Sprech-Session: Unterstuetzung pruefen, Token holen,
        Token-Dauer setzen. Danach kann send_speak_audio() aufgerufen werden.

        BUGFIX: 'speaktokentime' wurde bisher mit einem fest codierten Wert
        (urspruenglich 30, dann 2) aufgerufen -- die Kamera lehnte das mit
        'NG speaktokentime' ab. Der Parametername legt nahe, dass er sich auf
        den tatsaechlich von getspeaktoken zurueckgegebenen Token bezieht
        (z.B. 'OK getspeaktoken=3'), nicht auf eine freie Konstante. Der Token
        wird jetzt aus der Antwort geparst und fuer speaktokentime verwendet.
        Falls die Kamera das immer noch ablehnt, wird das NICHT mehr als
        fataler Fehler behandelt (siehe unten) -- getspeaktoken selbst hat ja
        bereits erfolgreich einen Token vergeben, der fuer send_speak_audio()
        vermutlich ausreicht."""
        self._get_flag("/vb.htm", "supporttwowayaudio")
        r = self._get_flag("/vb.htm", "getspeaktoken")
        token = self._parse_ok_value(r.text, "getspeaktoken")
        try:
            self._get("/vb.htm", params={"speaktokentime": token if token is not None else token_time_s})
        except CameraError as e:
            # Nicht fatal: getspeaktoken hat bereits einen gueltigen Token
            # geliefert, das reicht vermutlich fuer send_speak_audio(). Nur
            # loggen, nicht abbrechen.
            logger.debug("speaktokentime abgelehnt (nicht fatal): %s", e)
        self._speak_session = SpeakSession(token_time_s=token_time_s, started_at=time.time(), token=token)

    @staticmethod
    def _parse_ok_value(text: str, param: str) -> Optional[str]:
        """Extrahiert den Wert aus einer 'OK <param>=<wert>'-Zeile, falls
        vorhanden (z.B. 'OK getspeaktoken=3' -> '3')."""
        m = re.search(re.escape(f"OK {param}=") + r"([^\n\r\x00]*)", text)
        return m.group(1) if m else None

    # Aus einem Wireshark-Mitschnitt des ECHTEN Browser-Talk-Requests per
    # Byte-Analyse entschlüsseltes Frame-Format (siehe FRAME_HEADER_STRUCT):
    # Jedes Paket = 28-Byte-Header + bis zu 1000 Bytes Audionutzdaten.
    # Bestaetigt durch Beobachtung: Der 4-Byte-Magic-Wert wiederholte sich im
    # Mitschnitt exakt alle 1028 Bytes, die Sequenznummer (Byte 20-23) zaehlte
    # sauber 1,2,3,4,5... hoch, alle anderen Felder blieben zwischen den
    # Paketen konstant.
    _FRAME_MAGIC = b"\x95\x00\x00\x00"
    _FRAME_PAYLOAD_SIZE = 1000
    # Codec-ID-Byte (Frame-Offset 4): Wert 2 in ZWEI unabhaengigen Mitschnitten
    # bestaetigt -- einer mit Kamera-Einstellung "G.711", einer mit "G.726".
    # Beide Male war die ID identisch (2) UND die tatsaechlichen Audio-Bytes
    # zeigten beide Male die G.711-µ-law-typische Byte-Verteilung (0xff-
    # Haeufung). Der Talk-Kanal nutzt also unabhaengig vom Kamera-Codec immer
    # G.711 (µ-law) mit fester Codec-ID 2 -- keine Vermutung mehr, sondern
    # zweifach bestaetigt. G.726 wird fuer den Sprech-Kanal nicht unterstuetzt.
    _FRAME_CODEC_ID = {"G.726": 2}

    def _build_frame(self, payload: bytes, sequence: int, codec: str) -> bytes:
        """Baut ein einzelnes Sprech-Frame nach dem per Wireshark
        entschluesselten Format. payload wird bei Bedarf auf genau
        _FRAME_PAYLOAD_SIZE Bytes aufgefuellt (mit dem Stille-Byte des
        jeweiligen Codecs), da im Mitschnitt jedes Paket exakt 1000 Bytes
        Nutzdaten trug."""
        from audio_talk import CODEC_SILENCE_BYTE
        silence_byte = CODEC_SILENCE_BYTE.get(codec, b"\x00")
        if len(payload) < self._FRAME_PAYLOAD_SIZE:
            payload = payload + silence_byte * (self._FRAME_PAYLOAD_SIZE - len(payload))
        elif len(payload) > self._FRAME_PAYLOAD_SIZE:
            payload = payload[: self._FRAME_PAYLOAD_SIZE]

        codec_id = self._FRAME_CODEC_ID.get(codec, 2)
        header = (
            self._FRAME_MAGIC
            + bytes([codec_id, 1])  # codec_id, channels=1
            + struct.pack("<H", 8000)  # sample_rate
            + struct.pack("<H", len(payload))  # payload_len
            + struct.pack("<H", 16)  # unbekanntes, konstant beobachtetes Feld
            + b"\x00" * 8  # Padding (Byte 12-19)
            + struct.pack("<I", sequence)  # Sequenznummer
            + b"\x00" * 4  # Padding (Byte 24-27)
        )
        assert len(header) == 28
        return header + payload

    def open_speak_stream(self) -> "SpeakStreamHandle":
        """Oeffnet die Sprech-Verbindung EXAKT so, wie es der echte Browser
        laut Wireshark-Mitschnitt tut -- ueber einen ROHEN TCP-SOCKET, nicht
        ueber requests, UND mit dem per Byte-Analyse entschluesselten
        28-Byte-Frame-Header vor jedem 1000-Byte-Audioblock (siehe
        _build_frame()).

        Der per Wireshark verifizierte Original-Request des Browsers:

            POST /ipcam/speakstream.cgi HTTP/1.1
            Content-Length: 0
            User-Agent: InetURL/1.0
            Host: <ip>
            Connection: Keep-Alive
            Cache-Control: no-cache
            Cookie: chromeVerAlert=false; ActiveX=1
            Authorization: Basic <base64>

            <danach: gerahmte Audio-Frames, siehe _build_frame()>

        Der entscheidende Punkt: Die Kamera erwartet 'Content-Length: 0'
        (formal "kein Body"!) und bekommt die Audiodaten danach trotzdem als
        Folge von 1028-Byte-Paketen ueber dieselbe offene Verbindung
        geschoben. Das ist mit requests/urllib3 grundsaetzlich nicht
        abbildbar -- diese Bibliotheken beenden einen Request, sobald der
        deklarierte Body geschrieben ist.
        """
        if self._speak_session is None:
            raise CameraError("start_speak() muss vor open_speak_stream() aufgerufen werden.")

        credentials = base64.b64encode(
            f"{self.cfg.username}:{self.cfg.password}".encode("utf-8")
        ).decode("ascii")

        request_headers = (
            "POST /ipcam/speakstream.cgi HTTP/1.1\r\n"
            "Content-Length: 0\r\n"
            "User-Agent: InetURL/1.0\r\n"
            f"Host: {self.cfg.host}\r\n"
            "Connection: Keep-Alive\r\n"
            "Cache-Control: no-cache\r\n"
            "Cookie: chromeVerAlert=false; ActiveX=1\r\n"
            f"Authorization: Basic {credentials}\r\n"
            "\r\n"
        ).encode("ascii")

        q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        result: dict = {}
        codec = self._speak_codec

        def run():
            sock = None
            sequence = 1
            audio_buffer = b""
            try:
                sock = socket.create_connection(
                    (self.cfg.host, self.cfg.http_port), timeout=5.0
                )
                sock.sendall(request_headers)
                # Sofort ein Stille-Frame nachschieben, damit die Verbindung
                # nicht als tot eingestuft wird, waehrend ffmpeg noch anlaeuft.
                sock.sendall(self._build_frame(b"", sequence, codec))
                sequence += 1
                logger.debug(
                    "Speak-Socket geoeffnet zu %s:%s, Header (%d Bytes) + Stille-Frame gesendet",
                    self.cfg.host, self.cfg.http_port, len(request_headers),
                )
                sock.settimeout(None)  # Senden soll nicht durch Timeouts abbrechen
                total_sent = 0
                while True:
                    try:
                        # BUGFIX/VERSUCH: Timeout von vorher 100ms auf 500ms
                        # erhoeht. Verdacht: Bei kontinuierlicher Live-
                        # Mikrofonaufnahme (im Gegensatz zum Batch-Testton)
                        # kann ffmpeg's interne Verschluesselungs-Pufferung
                        # kurzzeitig stocken, obwohl real weiter Audiodaten
                        # unterwegs sind -- ein zu kurzer Timeout fuegt dann
                        # faelschlich ein Stille-Frame MITTEN in den
                        # Sprachfluss ein, was sich als periodisches Knacksen
                        # aeussern wuerde (passend zum beobachteten Symptom).
                        chunk = q.get(timeout=0.5)
                    except queue.Empty:
                        sock.sendall(self._build_frame(b"", sequence, codec))
                        sequence += 1
                        continue
                    if chunk is None:
                        break
                    audio_buffer += chunk
                    # In 1000-Byte-Payload-Fenstern rausschicken (Frame-Groesse
                    # aus dem Mitschnitt bestaetigt), Rest im Puffer behalten
                    while len(audio_buffer) >= self._FRAME_PAYLOAD_SIZE:
                        payload = audio_buffer[: self._FRAME_PAYLOAD_SIZE]
                        audio_buffer = audio_buffer[self._FRAME_PAYLOAD_SIZE :]
                        sock.sendall(self._build_frame(payload, sequence, codec))
                        sequence += 1
                        total_sent += len(payload)
                # Restbestand (kleiner als ein volles Frame) noch aufgefuellt senden
                if audio_buffer:
                    sock.sendall(self._build_frame(audio_buffer, sequence, codec))
                    total_sent += len(audio_buffer)
                result["bytes_sent"] = total_sent
                result["frames_sent"] = sequence
                logger.debug("Speak-Socket: %d Bytes Audio in %d Frames gesendet, wird geschlossen",
                             total_sent, sequence)
            except (OSError, socket.error) as e:
                result["error"] = str(e)
                logger.debug("Speak-Socket-Fehler: %s", e)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return SpeakStreamHandle(_queue=q, _thread=thread, _result=result)

    def stop_speak(self, token: Optional[int] = None) -> None:
        """BUGFIX: verwendete bisher einen fest codierten Token-Wert (2)
        statt des tatsaechlich von getspeaktoken zurueckgegebenen Tokens
        (z.B. '3') -- die Kamera lehnte das konsequent ab ('NG
        giveupspeaktoken'). Nutzt jetzt den in start_speak() gespeicherten
        echten Token. Ein Fehlschlag hier ist zudem nicht mehr fatal: das
        Sprechen soll clientseitig trotzdem als beendet gelten, auch wenn
        dieser Cleanup-Aufruf von der Kamera abgelehnt wird."""
        actual_token = token
        if actual_token is None and self._speak_session is not None:
            actual_token = self._speak_session.token
        if actual_token is None:
            actual_token = 2  # Fallback, falls nie ein Token geparst werden konnte
        try:
            self._get("/vb.htm", params={"giveupspeaktoken": actual_token})
        except CameraError as e:
            logger.debug("giveupspeaktoken abgelehnt (nicht fatal): %s", e)
        self._speak_session = None
