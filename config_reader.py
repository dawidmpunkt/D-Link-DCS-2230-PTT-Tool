"""
Liest die AKTUELL auf der Kamera aktiven Werte aus, indem die jeweilige
Setup-Seite live abgerufen und der eingebettete JS-Code geparst wird.

Funktionsprinzip: Die Kamera baut ihre Setup-Seiten serverseitig -- Platzhalter
wie <%brightness%> werden vor dem Ausliefern durch den tatsaechlich aktiven
Wert ersetzt. Ein frischer GET-Request auf z.B. setup_image.htm liefert also
JS-Code mit den echten aktuellen Werten direkt im Text, z.B.:
    new Ctrl_SelectNum("brightness",parseInt("0"),parseInt("8"),1,"4",...)
                                                                    ^^^ aktueller Wert

WICHTIG: Dieser Parser ist Heuristik/Best-Effort auf Basis von Regex-Mustern,
kein offiziell dokumentiertes API. Bei Firmware-Aenderungen oder abweichenden
Code-Pfaden (z.B. andere Ctrl_*-Varianten) kann die Erkennung fehlschlagen --
in dem Fall wird der jeweilige Wert einfach nicht befuellt (kein Absturz).
"""

import logging
import re
from typing import Optional

from camera_client import CameraClient, CameraError

logger = logging.getLogger("dcs2230.config_reader")


def _extract_value_before_param(html: str, param: str) -> Optional[str]:
    """Sucht das Muster ,"WERT","param="  (Standardform der meisten
    Ctrl_SelectNum/Ctrl_SelectEx/Ctrl_Radio/Ctrl_Text-Definitionen) und gibt
    WERT zurueck, falls gefunden."""
    pattern = re.escape(param) + r"="
    m = re.search(r',"([^"]*)"\s*,\s*"' + pattern, html)
    return m.group(1) if m else None


def _extract_global_var(html: str, var_name: str) -> Optional[str]:
    """Sucht 'var NAME = ...' und extrahiert den Wert, egal ob er als
    GV("wert",default), als Zahl oder als String vorliegt."""
    # var g_xxx = parseInt(GV("WERT", default))  oder  var g_xxx = GV("WERT", default)
    m = re.search(
        r"var\s+" + re.escape(var_name) + r"\s*=\s*(?:parseInt\()?GV\(\"([^\"]*)\"",
        html,
    )
    if m:
        return m.group(1)
    # var g_xxx = "WERT"  (direkter String, kein GV-Wrapper)
    m = re.search(r"var\s+" + re.escape(var_name) + r'\s*=\s*"([^"]*)"', html)
    if m:
        return m.group(1)
    # var g_xxx = 123  (nackte Zahl)
    m = re.search(r"var\s+" + re.escape(var_name) + r"\s*=\s*(-?\d+)", html)
    if m:
        return m.group(1)
    return None


def _extract_value_for_field(html: str, field_name: str, submit_param: str) -> Optional[str]:
    """Versucht zuerst den direkten Literal-Wert vor 'submit_param=' zu finden.
    Falls dort stattdessen ein Variablenname steht (z.B. bei
    Ctrl_SelectEx("exposuremode", liste, g_exposuretime, "exposuretime=")),
    wird die referenzierte Variable separat aufgeloest."""
    raw = _extract_value_before_param(html, submit_param)
    if raw is None:
        return None
    if raw.replace(".", "", 1).replace("-", "", 1).isdigit() or raw in ("0", "1"):
        return raw
    # Koennte ein Bezeichner statt eines Literals sein (selten bei diesem Muster,
    # aber sicherheitshalber versuchen)
    resolved = _extract_global_var(html, raw)
    return resolved if resolved is not None else raw


def _split_top_level_commas(s: str) -> list:
    """Teilt einen String an Kommas, die NICHT innerhalb von Klammern liegen
    -- noetig, weil GV("a","b") selbst Kommas enthaelt, die nicht als
    Array-Trenner zaehlen duerfen."""
    parts = []
    depth = 0
    current = ""
    for ch in s:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def _extract_array_value(html: str, array_name: str, index: int) -> Optional[str]:
    """Extrahiert den aktuellen Wert an Index `index` aus einem
    'var NAME = new Array(...)'-Konstrukt, egal ob der Eintrag ein
    literaler String ('"H.264"') oder GV(...)-gewrappt ist
    ('GV("JPEG",1)'). Gibt None zurueck, wenn der Eintrag ein nicht
    aufgeloester Platzhalter ('<%...%>') ist."""
    m = re.search(
        r"var\s+" + re.escape(array_name) + r"\s*=\s*new Array\((.*?)\);",
        html, re.DOTALL,
    )
    if not m:
        return None
    parts = _split_top_level_commas(m.group(1))
    if index >= len(parts):
        return None
    item = parts[index].strip()

    gv_match = re.match(r'GV\(\s*"([^"]*)"', item)
    if gv_match:
        val = gv_match.group(1)
        return None if val.startswith("<%") else val

    quoted_match = re.match(r'"([^"]*)"', item)
    if quoted_match:
        val = quoted_match.group(1)
        return None if val.startswith("<%") else val

    num_match = re.match(r"(-?\d+)", item)
    if num_match:
        return num_match.group(1)
    return None


def read_profile_video_settings(client: CameraClient, profile: int):
    """Liest die aktuellen Video-Einstellungen (Codec, Aufloesung, View
    Window, Framerate, qmode, Bitrate, Keyframe-Interval) fuer ein
    einzelnes Profil (1-3) aus setup_audio_video.htm."""
    result = CurrentSettings()
    index = profile - 1

    try:
        av_html = client.fetch_page("/setup_audio_video.htm")
        logger.debug("setup_audio_video.htm (Profil %d): %d Zeichen empfangen", profile, len(av_html))
    except CameraError as e:
        logger.debug("setup_audio_video.htm: Fehler beim Abruf: %s", e)
        av_html = ""

    profile_data = {}
    if av_html:
        profile_data["codec"] = _extract_array_value(av_html, "l_profileformat", index)
        profile_data["resolution"] = _extract_array_value(av_html, "l_profileresolution", index)
        profile_data["view_window"] = _extract_array_value(av_html, "l_profilereviewer", index)
        profile_data["framerate"] = _extract_array_value(av_html, "l_profilerate", index)
        qmode_val = _extract_array_value(av_html, "l_profileqmode", index)
        profile_data["constant_bitrate"] = (qmode_val == "0") if qmode_val is not None else None
        profile_data["bitrate"] = _extract_array_value(av_html, "l_profilebps", index)
        profile_data["keyframe_interval"] = _extract_array_value(av_html, "l_profilekeyframeinterval", index)

    result.profiles[profile] = profile_data

    if not any(v is not None for v in profile_data.values()):
        snippet = av_html[:200] if av_html else ""
        raise CameraError(
            f"Profil {profile}: kein einziges Feld gefunden. "
            f"Antwort begann mit: {snippet!r}"
        )
    return result


class CurrentSettings:
    """Container fuer erfolgreich ausgelesene aktuelle Werte. Felder, die
    nicht gefunden wurden, bleiben None -- die aufrufende GUI sollte in dem
    Fall das jeweilige Feld einfach unveraendert lassen."""

    def __init__(self):
        self.brightness: Optional[int] = None
        self.contrast: Optional[int] = None
        self.saturation: Optional[int] = None
        self.sharpness: Optional[int] = None
        self.denoise: Optional[int] = None
        self.exposure_mode: Optional[str] = None
        self.gain_db: Optional[int] = None
        self.aspect_ratio: Optional[str] = None

        self.profile_count: Optional[int] = None
        self.audio_in_muted: Optional[bool] = None
        self.audio_out_muted: Optional[bool] = None
        self.audio_out_volume: Optional[int] = None
        self.audio_codec: Optional[str] = None

        self.di_normally_closed: Optional[bool] = None
        self.ir_cut_mode: Optional[str] = None
        self.ir_led_mode: Optional[str] = None

        # profil -> dict mit codec/resolution/framerate/etc.
        self.profiles: dict = {}


def read_image_and_audio_settings(client: CameraClient) -> CurrentSettings:
    """Ruft setup_image.htm und setup_audio_video.htm ab und extrahiert
    Bildqualitaet-, Seitenverhaeltnis-, Profilanzahl- und Audio-Werte."""
    result = CurrentSettings()

    try:
        image_html = client.fetch_page("/setup_image.htm")
        logger.debug("setup_image.htm: %d Zeichen empfangen", len(image_html))
    except CameraError as e:
        logger.debug("setup_image.htm: Fehler beim Abruf: %s", e)
        image_html = ""

    if image_html:
        for attr, field in [
            ("brightness", "brightness"), ("contrast", "contrast"),
            ("saturation", "saturation"), ("sharpness", "sharpness"),
            ("denoise", "denoise"),
        ]:
            val = _extract_value_before_param(image_html, field)
            if val is not None:
                try:
                    setattr(result, attr, int(val))
                except ValueError:
                    pass

        exposure_val = _extract_global_var(image_html, "g_exposuretime")
        if exposure_val:
            result.exposure_mode = exposure_val

        gain_val = _extract_global_var(image_html, "g_agc")
        if gain_val:
            try:
                result.gain_db = int(gain_val)
            except ValueError:
                pass

    try:
        av_html = client.fetch_page("/setup_audio_video.htm")
        logger.debug("setup_audio_video.htm: %d Zeichen empfangen", len(av_html))
    except CameraError as e:
        logger.debug("setup_audio_video.htm: Fehler beim Abruf: %s", e)
        av_html = ""

    if av_html:
        aspect_val = _extract_global_var(av_html, "g_aspectratio")
        if aspect_val:
            result.aspect_ratio = aspect_val

        profile_count_val = _extract_global_var(av_html, "g_setProfileNumber")
        if profile_count_val:
            try:
                result.profile_count = int(profile_count_val)
            except ValueError:
                pass

        mute_val = _extract_global_var(av_html, "g_enmute")
        if mute_val is not None:
            result.audio_in_muted = (mute_val == "1")

        out_off_val = _extract_global_var(av_html, "g_audiooutoff")
        if out_off_val is not None:
            result.audio_out_muted = (out_off_val == "1")

        out_vol_val = _extract_global_var(av_html, "g_audiooutvolume")
        if out_vol_val:
            try:
                result.audio_out_volume = int(out_vol_val)
            except ValueError:
                pass

        # BUGFIX: Audio-Codec (G.711/G.726) wurde bisher gar nicht
        # extrahiert -- das Dropdown blieb beim Laden immer unveraendert.
        codec_val = _extract_value_before_param(av_html, "audiotype")
        if codec_val in ("G.711", "G.726"):
            result.audio_codec = codec_val

        # Pro-Profil-Werte (Codec, Aufloesung, Framerate) -- Suche nach den
        # jeweiligen g_-Arrays ist hier nicht zuverlaessig moeglich (Listen,
        # nicht Einzelwerte), daher nur die Basiswerte, die zuverlaessig als
        # Einzelvariable vorliegen, ausgelesen; Rest bleibt manuell zu pruefen.

    _raise_if_nothing_found(
        result,
        [image_html, av_html],
        fields=["brightness", "contrast", "saturation", "sharpness", "denoise",
                "exposure_mode", "gain_db", "aspect_ratio", "profile_count",
                "audio_in_muted", "audio_out_muted", "audio_out_volume"],
    )
    return result


def _raise_if_nothing_found(result: "CurrentSettings", fetched_pages: list, fields: list) -> None:
    """Wirft einen Fehler mit Diagnose-Snippet, falls trotz erfolgreichem
    Seitenabruf KEIN einziges Feld erkannt wurde -- typisches Symptom, wenn
    die Kamera z.B. statt der erwarteten Setup-Seite eine Login-Seite oder
    Fehlerseite zurueckgibt (HTTP 200, aber falscher Inhalt). Ohne diese
    Pruefung blieb das Formular in der GUI einfach unveraendert, ohne dass
    sichtbar wurde, dass etwas schiefgelaufen ist."""
    if any(getattr(result, f) is not None for f in fields):
        return  # mindestens ein Feld gefunden -- alles gut
    if not any(fetched_pages):
        return  # Seiten konnten gar nicht erst abgerufen werden -- das wurde
        # bereits als CameraError beim fetch_page()-Aufruf selbst gemeldet
    snippet = next((p[:200] for p in fetched_pages if p), "")
    raise CameraError(
        "Seite wurde abgerufen, aber kein einziges bekanntes Feld gefunden. "
        "Das deutet darauf hin, dass die Kamera nicht die erwartete Setup-Seite "
        "zurueckgegeben hat (z.B. Login-Seite statt Konfigurationsseite). "
        f"Antwort begann mit: {snippet!r}"
    )


def read_io_ir_settings(client: CameraClient) -> CurrentSettings:
    """Ruft setup_digital_io.htm und setup_icr.htm ab und extrahiert
    DI-Polaritaet, IR-Cut-Modus und IR-LED-Modus."""
    result = CurrentSettings()

    try:
        io_html = client.fetch_page("/setup_digital_io.htm")
        logger.debug("setup_digital_io.htm: %d Zeichen empfangen", len(io_html))
    except CameraError as e:
        logger.debug("setup_digital_io.htm: Fehler beim Abruf: %s", e)
        io_html = ""
    if io_html:
        # l_giointype[0] als aktueller Wert des einzigen Digitaleingangs.
        # Das Array ist mehrzeilig und der erste Eintrag steckt in einem
        # GV(...)-Wrapper, z.B.:
        #   var l_giointype = new Array(
        #       GV("0",0),
        #       GV("<%giointype.2%>",0), ...
        m = re.search(
            r'var\s+l_giointype\s*=\s*new Array\(\s*GV\("(\d+)"',
            io_html,
        )
        if m:
            result.di_normally_closed = (m.group(1) == "1")

    try:
        icr_html = client.fetch_page("/setup_icr.htm")
        logger.debug("setup_icr.htm: %d Zeichen empfangen", len(icr_html))
    except CameraError as e:
        logger.debug("setup_icr.htm: Fehler beim Abruf: %s", e)
        icr_html = ""
    if icr_html:
        mode_val = _extract_global_var(icr_html, "g_dncontrolmode")
        if mode_val:
            reverse_map = {"0": "automatic", "1": "day", "2": "night", "3": "schedule"}
            result.ir_cut_mode = reverse_map.get(mode_val)

        led_val = _extract_global_var(icr_html, "g_ledmode")
        if led_val:
            reverse_map_led = {"0": "off", "1": "on", "2": "automatic", "3": "schedule"}
            result.ir_led_mode = reverse_map_led.get(led_val)

    _raise_if_nothing_found(
        result,
        [io_html, icr_html],
        fields=["di_normally_closed", "ir_cut_mode", "ir_led_mode"],
    )
    return result
