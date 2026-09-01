"""
Konfigurationsverwaltung fuer das DCS-2230 Kontroll-Tool.

Speichert Zugangsdaten, Kamera-Adresse und Einstellungen lokal als JSON-Datei
unter %APPDATA%/dcs2230_tool/config.json (Windows) bzw. ~/.dcs2230_tool/config.json
(Linux/Mac).

WICHTIGER SICHERHEITSHINWEIS: Das Passwort wird aktuell im Klartext in der
JSON-Datei gespeichert. Das ist fuer eine Kamera im eigenen, vertrauenswuerdigen
LAN mit ohnehin leerem Passwort ein akzeptabler Kompromiss, aber KEIN sicherer
Passwortspeicher. Falls das Kamera-Passwort mit einem anderen, sensiblen Passwort
identisch ist: unbedingt aendern. Bei Bedarf kann hier spaeter das `keyring`-Paket
fuer eine OS-Keychain-Speicherung nachgeruestet werden.
"""

import json
import os
import platform
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Optional


def _default_config_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "dcs2230_tool"
    return Path.home() / ".dcs2230_tool"


CONFIG_DIR = _default_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class CameraConfig:
    host: str = "192.168.0.103"
    http_port: int = 80
    rtsp_port: int = 554
    username: str = "admin"
    password: str = ""
    # RTSP-Profil fuer den "URL kopieren"-Button (zum Ansehen in externem VLC).
    stream_profile: int = 1
    # Globaler Hotkey fuer Push-to-Talk, Syntax nach der 'keyboard'-Bibliothek,
    # z.B. "f9", "capslock", "ctrl+space". Leerer String = kein Hotkey.
    ptt_hotkey: str = "f9"
    # Audio-Codec fuer Push-to-Talk. Funktioniert in jeder Kombination nur mit G.726. Das
    # ist eine Hardware-/Firmware-Eigenschaft dieser Kamera, kein Bug in
    # diesem Tool -- G.711 fuer PTT ist auf diesem Geraet nicht erreichbar.
    audio_codec: str = "G.726"
    # Ob ein extern laufender VLC-Prozess waehrend des Sprechens automatisch
    # stummgeschaltet werden soll (verhindert Rueckkopplung/Echo, da Video/
    # Audio jetzt in einem separaten VLC angesehen wird, nicht mehr im Tool
    # eingebettet). Nutzt Windows' pro-Anwendung-Lautstaerkeregelung (pycaw).
    ptt_mute_vlc_process: bool = True
    # Prozessname, der beim Sprechen stummgeschaltet wird. Anpassen, falls
    # eine andere VLC-Variante/ein anderer Player verwendet wird.
    vlc_process_name: str = "vlc.exe"
    # UI-Sprache: "en" (Standard) oder "de"
    language: str = "en"
    # True = Taste/Hotkey muss gehalten werden (Hold-to-Talk), False = einmal
    # klicken/druecken schaltet um (Toggle-Modus)
    ptt_hold_mode: bool = True
    # True = Hotkey wirkt systemweit (ueber 'keyboard'-Bibliothek), False =
    # nur wenn das Tool-Fenster den Fokus hat (reines Qt-Tastaturereignis)
    ptt_hotkey_global: bool = True
    # VLC network-caching-Wert (ms), wird beim Kopieren der RTSP-URL mit
    # angehaengt (siehe main.py _copy_rtsp_url)
    vlc_network_caching: int = 10

    @property
    def rtsp_url(self) -> str:
        # Doppelter Slash vor live<n>.sdp ist fuer die DCS-2230 bestaetigt
        # notwendig (siehe vorherige Recherche).
        auth = ""
        if self.username:
            auth = f"{self.username}:{self.password}@"
        return f"rtsp://{auth}{self.host}:{self.rtsp_port}//live{self.stream_profile}.sdp"

    @property
    def http_base_url(self) -> str:
        return f"http://{self.host}:{self.http_port}"


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def load_config() -> Optional[CameraConfig]:
    """Laedt die Konfiguration, falls vorhanden. Gibt None zurueck, wenn keine
    Config-Datei existiert oder sie nicht lesbar/gueltig ist.

    Filtert unbekannte Schluessel heraus, statt bei einer Typenkonflikt-
    Exception zu scheitern -- so fuehrt das Entfernen alter Felder (z.B.
    network_caching_ms, ptt_mute_output_while_talking beim Wegfall der
    eingebetteten Video-Anzeige) nicht dazu, dass eine bestehende
    config.json ploetzlich komplett unlesbar wird."""
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        known_fields = {f.name for f in fields(CameraConfig)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return CameraConfig(**filtered)
    except (json.JSONDecodeError, TypeError, OSError) as e:
        print(f"[Konfiguration] Konnte {CONFIG_FILE} nicht laden: {e}")
        return None


def save_config(cfg: CameraConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[Konfiguration] Gespeichert nach {CONFIG_FILE}")
