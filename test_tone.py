"""
Test-Sinuston zur Kamera streamen
----------------------------------
Erzeugt einen reinen Sinuston (Standard 1000 Hz) und sendet ihn ueber
denselben Mechanismus wie Push-to-Talk an die Kamera -- unabhaengig vom
Mikrofon. Damit laesst sich testen, ob das Streaming/Encoding an sich
funktioniert, ohne dass Mikrofon-Probleme (falsches Geraet, Krackseln durch
Sample-Rate-Konvertierung etc.) die Fehlersuche verfaelschen.

Nutzung:
    python test_tone.py --codec G.711-ulaw
    python test_tone.py --codec G.726 --freq 440 --duration 3
    python test_tone.py --list-codecs

Nutzt automatisch die gespeicherte Kamera-Konfiguration (config.json), falls
vorhanden -- sonst bitte --host/--user/--password angeben.
"""

import argparse
import subprocess
import sys
import time

import numpy as np

from config import load_config, CameraConfig
from camera_client import CameraClient, CameraError
from audio_talk import CODEC_FFMPEG_ARGS, TARGET_SAMPLE_RATE, ffmpeg_available


def generate_sine_pcm(freq_hz: float, duration_s: float, sample_rate: int, amplitude_pct: float = 25.0) -> bytes:
    """Erzeugt rohes 16-bit PCM Mono eines reinen Sinustons.

    amplitude_pct: Prozent der Vollaussteuerung (Default 25%, deutlich
    niedriger als vorher (70%) -- Verdacht: die Kamera wendet intern
    zusaetzlichen Gain an (in Erwartung leiserer echter Sprache), wodurch
    ein lauter Vollausschlag-Testton am Lautsprecher uebersteuert/verzerrt
    ankommt."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    amplitude = (amplitude_pct / 100.0) * 32767
    tone = (np.sin(2 * np.pi * freq_hz * t) * amplitude).astype(np.int16)
    return tone.tobytes()


def encode_pcm(pcm_bytes: bytes, codec: str, sample_rate: int, invert_bits: bool = False) -> bytes:
    """Kodiert rohes PCM ueber ffmpeg in den gewuenschten Codec (gleiche
    Codec-Tabelle wie audio_talk.py, damit Testton und echtes PTT-Audio
    identisch kodiert werden).

    invert_bits: XOR jedes kodierten Bytes mit 0xff. TESTHYPOTHESE: manche
    µ-law-Implementierungen invertieren die Bits vor der Uebertragung
    (historisch fuer DC-Balance auf Telefonleitungen), ffmpegs pcm_mulaw tut
    das nicht. Falls die Kamera die invertierte Konvention erwartet, waere
    das eine Erklaerung fuer 'richtige Tonhoehe, aber verzerrt' (die
    Grundfrequenz uebersteht die Bit-Verwuerfelung, die Wellenform nicht)."""
    if codec not in CODEC_FFMPEG_ARGS:
        raise ValueError(f"Unbekannter Codec: {codec}. Verfuegbar: {list(CODEC_FFMPEG_ARGS.keys())}")
    codec_args = CODEC_FFMPEG_ARGS[codec]
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(sample_rate), "-ac", "1",
            "-i", "pipe:0",
            *codec_args, "pipe:1",
        ],
        input=pcm_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg-Fehler: {proc.stderr.decode(errors='replace')}")
    encoded = proc.stdout
    if invert_bits:
        encoded = bytes(b ^ 0xFF for b in encoded)
    return encoded


def main():
    parser = argparse.ArgumentParser(description="Testton zur Kamera streamen")
    parser.add_argument("--host", help="Kamera-IP (Default: aus config.json)")
    parser.add_argument("--user", help="Benutzername (Default: aus config.json)")
    parser.add_argument("--password", help="Passwort (Default: aus config.json)")
    parser.add_argument("--codec", default="G.711-ulaw",
                         help=f"Codec (Default: G.711-ulaw). Verfuegbar: {list(CODEC_FFMPEG_ARGS.keys())}")
    parser.add_argument("--freq", type=float, default=1000.0, help="Frequenz in Hz (Default: 1000)")
    parser.add_argument("--duration", type=float, default=5.0, help="Dauer in Sekunden (Default: 5)")
    parser.add_argument("--amplitude", type=float, default=25.0,
                         help="Lautstaerke in %% der Vollaussteuerung (Default: 25)")
    parser.add_argument("--invert-bits", action="store_true",
                         help="Testet die Bit-Invertierungs-Hypothese fuer µ-law "
                              "(XOR jedes kodierten Bytes mit 0xff)")
    parser.add_argument("--list-codecs", action="store_true", help="Verfuegbare Codecs auflisten und beenden")
    args = parser.parse_args()

    if args.list_codecs:
        print("Verfuegbare Codecs:")
        for name in CODEC_FFMPEG_ARGS:
            print(f"  {name}")
        sys.exit(0)

    if not ffmpeg_available():
        sys.exit("FEHLER: ffmpeg nicht im PATH gefunden.")

    cfg = load_config()
    if cfg is None:
        if not (args.host and args.user is not None and args.password is not None):
            sys.exit(
                "Keine config.json gefunden und nicht alle Verbindungsdaten "
                "per Argument angegeben. Bitte --host/--user/--password setzen."
            )
        cfg = CameraConfig(host=args.host, username=args.user, password=args.password)
    if args.host:
        cfg.host = args.host
    if args.user is not None:
        cfg.username = args.user
    if args.password is not None:
        cfg.password = args.password

    print(f"Verbinde zu {cfg.host} ...")
    client = CameraClient(cfg)
    try:
        method = client.detect_auth()
        print(f"Verbunden ({method}-Auth).")
    except CameraError as e:
        sys.exit(f"Verbindungsfehler: {e}")

    print(f"Erzeuge {args.freq} Hz Sinuston, {args.duration}s, Codec {args.codec} ...")
    pcm = generate_sine_pcm(args.freq, args.duration, TARGET_SAMPLE_RATE, args.amplitude)
    encoded = encode_pcm(pcm, args.codec, TARGET_SAMPLE_RATE, args.invert_bits)
    print(f"Kodiert: {len(encoded)} Bytes ({args.codec})")

    client._speak_codec = args.codec
    try:
        client.start_speak()
    except CameraError as e:
        sys.exit(f"start_speak() fehlgeschlagen: {e}")

    print("Oeffne Sprech-Verbindung und sende Ton ...")
    handle = client.open_speak_stream()

    # In 1000-Byte-Bloecken senden (passend zur Frame-Payload-Groesse), OHNE
    # codec-abhaengige Drosselung. VERDACHT (zu testen): Die Kamera hat
    # vermutlich eine FESTE Zeitannahme pro 1000-Byte-Frame (~125ms, aus dem
    # echten G.711-Browser-Mitschnitt bekannt), unabhaengig vom tatsaechlichen
    # Codec-Bitrate. Die vorherige codec-abhaengige Drosselung (langsamer fuer
    # niedrigere Bitraten wie G.726) koennte genau das Tonhoehen-/Pausen-
    # Problem verursacht haben. Kleine feste Pause nur, um die Verbindung
    # nicht mit einem einzigen Riesen-Burst zu fluten.
    chunk_size = 1000
    for i in range(0, len(encoded), chunk_size):
        chunk = encoded[i:i + chunk_size]
        handle.write(chunk)
        time.sleep(0.05)  # kleine feste Pause, unabhaengig vom Codec

    print("Ton komplett gesendet, schliesse Verbindung ...")
    result = handle.close()
    print("Ergebnis:", result)

    try:
        client.stop_speak()
    except CameraError as e:
        print(f"stop_speak()-Warnung (nicht fatal): {e}")

    print("Fertig. War der Ton am Kameralautsprecher zu hoeren?")


if __name__ == "__main__":
    main()
