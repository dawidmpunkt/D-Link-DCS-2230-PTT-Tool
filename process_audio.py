"""
Prozess-spezifische Lautstaerkesteuerung (Windows).

Nutzt pycaw (Python Core Audio Windows Library), um gezielt EINEN laufenden
Prozess (z.B. vlc.exe) stummzuschalten -- unabhaengig davon, wie er gestartet
wurde. Das ist dieselbe pro-Anwendung-Lautstaerkeregelung, die auch im
Windows-Lautstaerkemixer zu sehen ist.

Nur unter Windows verfuegbar. Auf anderen Plattformen ist PYCAW_AVAILABLE
False, alle Funktionen werden dann zu No-Ops.
"""

import logging

logger = logging.getLogger("dcs2230.process_audio")

try:
    from pycaw.pycaw import AudioUtilities
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False


def set_process_mute(process_name: str, mute: bool) -> bool:
    """Mutet/entmutet alle laufenden Audio-Sessions eines Prozesses
    (Vergleich case-insensitive, z.B. 'vlc.exe'). Gibt True zurueck, wenn
    mindestens eine passende Session gefunden und umgeschaltet wurde."""
    if not PYCAW_AVAILABLE:
        logger.debug("pycaw nicht installiert -- Prozess-Mute uebersprungen")
        return False

    found = False
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as e:
        logger.debug("Konnte Audio-Sessions nicht abrufen: %s", e)
        return False

    for session in sessions:
        proc = session.Process
        if proc is None:
            continue
        try:
            name = proc.name()
        except Exception:
            continue
        if name.lower() == process_name.lower():
            try:
                session.SimpleAudioVolume.SetMute(1 if mute else 0, None)
                found = True
                logger.debug("%s (PID %s): %s", name, proc.pid,
                             "stummgeschaltet" if mute else "wieder hoerbar")
            except Exception as e:
                logger.debug("Mute fuer %s (PID %s) fehlgeschlagen: %s", name, proc.pid, e)

    if not found:
        logger.debug("Kein laufender Prozess '%s' mit aktiver Audio-Session gefunden", process_name)
    return found
