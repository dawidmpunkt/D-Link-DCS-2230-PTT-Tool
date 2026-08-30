"""
Push-to-Talk Audio-Modul.

Nimmt Mikrofonaudio auf (sounddevice), kodiert es nach G.726 (via ffmpeg-
Subprozess, da Python keinen nativen G.726-Encoder mitbringt) und schreibt es
fortlaufend in EINE offene Streaming-Verbindung zur Kamera (siehe
camera_client.open_speak_stream()).

WICHTIG: Das genaue vom speakstream.cgi erwartete Format (Content-Type,
Chunking, evtl. Header vor den Audiodaten) ist NICHT verifiziert -- siehe
Hinweis in camera_client.py. Dieses Modul ist ein plausibler Versuch, kein
bestaetigt funktionierender Client.

Voraussetzung: ffmpeg muss im PATH verfuegbar sein (`ffmpeg -version` testen).
"""

import logging
import shutil
import subprocess
import threading
from typing import Any, Callable, Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger("dcs2230.audio_talk")

CAPTURE_SAMPLE_RATE = 48000  # Aufnahmerate: von praktisch jeder Hardware sauber unterstuetzt.
                              # BUGFIX: Vorher wurde direkt mit 8000 Hz aufgenommen (der von
                              # der Kamera benoetigten Zielrate) -- viele Windows-Audiotreiber/
                              # USB-Mikrofone kommen mit so niedrigen, unueblichen Raten auf
                              # Hardware-Ebene schlecht klar, was sich als Krackseln/Rauschen
                              # aeusserte, ohne einen klaren Fehler zu werfen.
TARGET_SAMPLE_RATE = 8000    # von der Kamera benoetigte Rate -- ffmpeg resampled sauber dorthin
CHANNELS = 1
BLOCK_DURATION_S = 0.05  # Blockgroesse fuer Aufnahme/Versand (vorher 0.2 -- fuer
                          # niedrigere Latenz deutlich verkleinert, siehe Latenz-
                          # Diskussion in PushToTalkSession)

# ffmpeg-Encoder-Parameter je Codec-Variante.
#
# STAND DER DINGE (per Wireshark-Mitschnitt bestaetigt): Der Sprech-Kanal der
# Kamera erwartet IMMER G.711 µ-law, unabhaengig vom Kamera-seitigen
# audiotype (der nur die Zuhoeren-Richtung betrifft). G.711 A-law fuehrt zu
# einem aktiven Verbindungsabbruch (die Kamera erkennt es vermutlich als
# beschaedigten/falschen Stream). G.726 fuer den Sprech-Kanal nicht
# unterstuetzt. Die anderen Varianten unten bleiben zum Vergleich/Diagnose.
CODEC_FFMPEG_ARGS = {
    "G.726": ["-c:a", "g726", "-b:a", "32k", "-f", "g726"],
}

# Stille-Byte je Codec (fuer den Verbindungs-Keepalive in camera_client)
CODEC_SILENCE_BYTE = {
    "G.726": b"\x00",
}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def list_input_devices() -> list:
    """Gibt alle verfuegbaren Eingabe-(Mikrofon-)Geraete zurueck als Liste
    von (index, anzeigename)-Tupeln.

    Wichtig fuer die Fehlersuche: sounddevice/PortAudio hat unter Windows
    mehrere Audio-Backends (MME, DirectSound, WASAPI, WDM-KS), die
    UNTERSCHIEDLICHE Vorstellungen von 'Standardgeraet' haben koennen --
    das muss nicht zwingend mit der in den Windows-Sound-Einstellungen
    gewaehlten Standard-Aufnahme uebereinstimmen. Explizite Auswahl ist
    daher zuverlaessiger als sich auf 'den Standard' zu verlassen."""
    devices = []
    try:
        hostapis = sd.query_hostapis()
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                hostapi_name = hostapis[dev["hostapi"]]["name"] if dev.get("hostapi") is not None else ""
                devices.append((idx, f"{dev['name']} [{hostapi_name}]"))
    except Exception as e:
        logger.debug("Konnte Audiogeraete nicht auflisten: %s", e)
    return devices


class PushToTalkSession:
    """Startet/stoppt eine Mikrofon-Aufnahme- und Sende-Session.

    Nutzung:
        session = PushToTalkSession(open_stream_callback=client.open_speak_stream,
                                     error_callback=lambda e: print(e))
        session.start()   # bei Tastendruck
        ...
        session.stop()    # bei Loslassen
    """

    def __init__(
        self,
        open_stream_callback: Callable[[], Any],
        error_callback: Optional[Callable[[Exception], None]] = None,
        codec: str = "G.726",
        device: Optional[int] = None,
        dsp_enabled: bool = True,
    ):
        self._open_stream_callback = open_stream_callback
        self._error_callback = error_callback
        self._codec = codec
        self._device = device  # None = PortAudio-Standard (siehe list_input_devices())
        self._dsp_enabled = dsp_enabled
        self._stream: Optional[sd.InputStream] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._sender_thread: Optional[threading.Thread] = None
        self._running = False
        self._speak_stream_handle = None  # camera_client.SpeakStreamHandle
        self._gain = 1.0  # Verstaerkungsfaktor fuer das Mikrofonsignal (1.0 = unveraendert)

    def set_dsp_enabled(self, enabled: bool) -> None:
        """Schaltet die DSP-Filterkette (Hochpass+Kompressor+Limiter) an/aus.
        Wirkt erst ab der naechsten Sprech-Session."""
        self._dsp_enabled = enabled

    def set_device(self, device: Optional[int]) -> None:
        """Setzt das Aufnahmegeraet explizit (Index aus list_input_devices()).
        None = PortAudio-Standard. Wirkt erst ab der naechsten Session."""
        self._device = device

    def set_codec(self, codec: str) -> None:
        """Setzt den Codec, mit dem ffmpeg kodiert. Muss zur Kamera-Einstellung
        passen. Wirkt erst ab der naechsten Sprech-Session."""
        if codec not in CODEC_FFMPEG_ARGS:
            raise ValueError(f"Unbekannter Codec: {codec}")
        self._codec = codec

    def set_gain(self, gain: float) -> None:
        """Setzt den Verstaerkungsfaktor fuer das Mikrofon des Client-PCs
        (z.B. 0.5 = leiser, 2.0 = doppelt so laut). Wirkt sich sofort auf
        eine laufende Session aus, kann also auch waehrend des Sprechens
        live nachgeregelt werden."""
        self._gain = max(0.0, gain)

    def start(self) -> None:
        if self._running:
            return
        if not ffmpeg_available():
            raise RuntimeError(
                "ffmpeg wurde nicht im PATH gefunden. Fuer die G.726-Kodierung "
                "wird ffmpeg benoetigt -- https://ffmpeg.org/download.html "
                "installieren und sicherstellen, dass 'ffmpeg' im PATH liegt."
            )
        self._running = True
        self._speak_stream_handle = self._open_stream_callback()

        # Diagnose: protokollieren, welches Geraet tatsaechlich benutzt wird
        # -- wichtig, weil PortAudios 'Standardgeraet' nicht immer mit dem
        # in Windows als Standard-Mikrofon eingestellten Geraet uebereinstimmt.
        try:
            if self._device is not None:
                dev_info = sd.query_devices(self._device)
                logger.debug("PTT nutzt explizit gewaehltes Geraet #%d: %s",
                             self._device, dev_info["name"])
            else:
                dev_info = sd.query_devices(kind="input")
                logger.debug("PTT nutzt PortAudio-Standardgeraet: %s "
                             "(kann von der Windows-Standardaufnahme abweichen!)",
                             dev_info["name"])
        except Exception as e:
            logger.debug("Geraete-Diagnose fehlgeschlagen: %s", e)

        # ffmpeg liest rohes 16-bit PCM Mono @ CAPTURE_SAMPLE_RATE von stdin,
        # resampled intern auf TARGET_SAMPLE_RATE (8kHz, von der Kamera
        # benoetigt) und schreibt die kodierten Bytes im konfigurierten
        # Codec auf stdout. Explizites '-ar TARGET_SAMPLE_RATE' vor den
        # Codec-Args erzwingt den Resample-Schritt zuverlaessig.
        codec_args = CODEC_FFMPEG_ARGS[self._codec]
        filter_args = []
        if self._dsp_enabled:
            # DSP-Kette fuer klarere Sprache ueber Telefonie-Codecs:
            #  1. highpass=200: schneidet Rumpeln/Trittschall unter 200 Hz weg
            #     (menschliche Sprache-Grundfrequenz liegt deutlich darueber)
            #  2. acompressor: gleicht Pegelschwankungen aus (leise Stellen
            #     werden angehoben, laute gedaempft) -- macht Sprache ueber
            #     schmalbandige Codecs wie G.726 deutlich verstaendlicher
            #  3. alimiter: haerte Pegel-Obergrenze als Schutz vor Uebersteuerung
            #     (relevant, da wir gesehen haben, dass die Kamera empfindlich
            #     auf zu lauten Pegel reagiert -- siehe test_tone.py-Tests)
            filter_args = [
                "-af",
                "highpass=f=200,"
                "acompressor=threshold=0.1:ratio=4:attack=5:release=100:makeup=2,"
                "alimiter=limit=0.7",
            ]
        self._ffmpeg_proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner", "-loglevel", "error",
                "-f", "s16le", "-ar", str(CAPTURE_SAMPLE_RATE), "-ac", str(CHANNELS),
                "-i", "pipe:0",
                *filter_args,
                "-ar", str(TARGET_SAMPLE_RATE),
                *codec_args, "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def audio_callback(indata: np.ndarray, frames: int, time_info, status):
            if status:
                # z.B. Input-Overflow -- nicht fatal, nur loggen
                print(f"[PTT] sounddevice-Status: {status}")
            if self._ffmpeg_proc and self._ffmpeg_proc.stdin:
                data = indata
                if self._gain != 1.0:
                    # Gain anwenden, dabei vor int16-Ueberlauf (Clipping) schuetzen
                    scaled = data.astype(np.float32) * self._gain
                    data = np.clip(scaled, -32768, 32767).astype(np.int16)
                try:
                    self._ffmpeg_proc.stdin.write(data.tobytes())
                except (BrokenPipeError, OSError):
                    pass  # ffmpeg evtl. schon beendet

        self._stream = sd.InputStream(
            samplerate=CAPTURE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=int(CAPTURE_SAMPLE_RATE * BLOCK_DURATION_S),
            callback=audio_callback,
            device=self._device,
        )
        self._stream.start()

        self._sender_thread = threading.Thread(target=self._read_and_send_loop, daemon=True)
        self._sender_thread.start()

    def _read_and_send_loop(self) -> None:
        """Liest kodierte Audio-Bloecke von ffmpeg und schreibt sie in die
        offene Streaming-Verbindung.

        BUGFIX: Nutzt read1() statt read(n). read(n) wartet, bis EXAKT n Bytes
        verfuegbar sind, und blockiert dadurch bei einem kontinuierlichen
        Live-Stream oft dauerhaft -- im Log zeigte sich das als
        'Speak-Socket: 0 Bytes Audio gesendet', obwohl das Mikrofon lief.
        read1() liefert dagegen sofort, was gerade im Puffer liegt."""
        assert self._ffmpeg_proc is not None
        while self._running:
            try:
                data = self._ffmpeg_proc.stdout.read1(4096)
            except (OSError, ValueError):
                break
            if not data:
                break
            try:
                self._speak_stream_handle.write(data)
            except Exception as e:  # noqa: BLE001 -- an GUI weiterreichen statt Thread sterben lassen
                if self._error_callback:
                    self._error_callback(e)
                break

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._ffmpeg_proc is not None:
            try:
                if self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.close()
            except OSError:
                pass
            self._ffmpeg_proc.terminate()
            try:
                self._ffmpeg_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._ffmpeg_proc.kill()
            self._ffmpeg_proc = None

        if self._sender_thread is not None:
            self._sender_thread.join(timeout=2)
            self._sender_thread = None

        if self._speak_stream_handle is not None:
            result = self._speak_stream_handle.close()
            self._speak_stream_handle = None
            if "error" in result and self._error_callback:
                self._error_callback(RuntimeError(f"Speak-Stream-Fehler: {result['error']}"))
