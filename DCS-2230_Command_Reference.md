# D-Link DCS-2230 — Kommandoreferenz

Erstellt aus der Analyse von `index.js`, `ptz-dlink.js`, `ptzctrl.js`, `pelcod.js`,
`var.js`, `common.js`, `D-Link.js` sowie den rohen HTML-Quelltexten aller
Setup-/Status-/Maintenance-Seiten. Ergänzt durch Wireshark-Mitschnitte der
tatsächlichen Nutzung.

**Legende:**
- ✅ **Bestätigt** — per Wireshark-Mitschnitt live beobachtet
- 📄 **Nur Code** — im JS/HTML referenziert, aber nicht live getestet/bestätigt
- ⚠️ **Inaktiv bei diesem Modell** — Code vorhanden, aber laut `var.js`-Feature-Flags
  für dieses Kameramodell deaktiviert (gemeinsame Codebasis mit anderen D-Link-PTZ-Kameras)

Basis-URL-Mechanismus für die meisten Einstellungs-Änderungen:
`c_iniUrl = "/vb.htm?language=ie"` (definiert in `common.js`), an die pro Aktion
`&parameter=wert`-Paare angehängt werden, z. B. `/vb.htm?language=ie&giooutalwayson=1:1`.

---

## 1. Audio (Sprechen / Zuhören) ✅

| Aktion | Request |
|---|---|
| Zwei-Wege-Audio-Unterstützung prüfen | `GET /vb.htm?supporttwowayaudio` |
| Sprech-Token anfordern | `GET /vb.htm?getspeaktoken` |
| Token-Gültigkeitsdauer setzen | `GET /vb.htm?speaktokentime=<n>` |
| **Audio hochladen (Sprechen)** | `POST /ipcam/speakstream.cgi` |
| Sprechen beenden | `GET /vb.htm?giveupspeaktoken=<n>` |
| Zuhören ein/aus | Rein client-seitig über ActiveX-Objekt (`AudioPlayerEnable`), kein separater Request |
| Audio-Codec | G.726 (aus `var.js`: `g_audioType = "G.726"`) |

## 2. Digitales PTZ (ePTZ) — aktiv bei diesem Modell, ECHTE Server-Kommunikation bestätigt

**Wichtige Klärung:** Zwischenzeitlich gab es Zweifel, ob "Bewegen" nicht doch rein
client-seitig laeuft (analog zu Zoom). Nach Behebung eines Authentifizierungs-Bugs
im eigenen Tool ist jetzt klar bestätigt: **Die Kamera nimmt die Richtungsbefehle
entgegen und liefert echte, richtungsabhängige Positions-Deltas zurück**
(`OK goto=0000-020` bei "up", `OK goto=-0200000` bei "left", `OK goto=00200000`
bei "right", `OK goto=00200020` bei "right_down" -- die Werte unterscheiden sich
klar je nach Richtung und Kombination). Es ist also **kein** rein client-seitiger
Effekt wie beim Zoom.

| Aktion | Request | Status |
|---|---|---|
| Auto-Pan starten | `GET /cgi-bin/eptzpreset.cgi?action=autopan&streamid=<n>` | ✅ |
| Stop | `GET /cgi-bin/eptzpreset.cgi?action=stop&streamid=<n>` | ✅ |
| Sequenz starten | `GET /cgi-bin/eptzpreset.cgi?action=seq_go&streamid=<n>` | 📄 |
| Zu Preset springen | `GET /cgi-bin/eptzpreset.cgi?action=goto&streamid=<n>&name=<name>&direction=point` | 📄 |
| Richtungstasten | `GET /cgi-bin/eptzpreset.cgi?action=goto&streamid=<n>&direction=up\|down\|left\|right\|left_up\|right_up\|left_down\|right_down\|home` | ✅ bestätigt, liefert `OK goto=<8-stelliges Delta>` |
| Freies Ziel (Klick ins Bild) | `GET /vb.htm?eptzcoordinate=<streamid><x4stellig><y4stellig>` | 📄 |

## 3. Zoom — GEKLÄRT: reines Client-seitiges Software-Zoom, keine Kamera-Kommunikation nötig

**Ergebnis der Untersuchung:** Der sichtbare Zoom-Effekt ist **vollständig lokal im
Browser-Plugin** implementiert, nicht auf der Kamera. Der entscheidende Code findet sich in
(`ptz-dlink.js`). Die Methode `SetZoomSize()` ist Teil des lokalen Video-Rendering-Plugins, nicht der
Kamera. 

Also:
Der sichtbare Zoom-Effekt läuft vollständig client-seitig: Der Zoom-Button ruft SetZoomSize() auf dem 
lokalen ActiveX-Videoplayer-Objekt auf und erhöht dabei einen internen Zähler. Die Methode beschneidet/skaliert 
lediglich den bereits empfangenen, dekodierten Frame im Browser-Plugin, ohne dass dafür ein Request an die Kamera 
nötig wäre.

Das deckt sich mit den offiziellen Spezifikationen: Die DCS-2230 hat eine
**Festbrennweite von 4,37 mm** (kein mechanisches Zoomobjektiv) und **10-fachen
digitalen Zoom** (offizielles D-Link-Handbuch:
https://manuals.plus/m/eadb19ed1a5e61f05c11f44c067b21de2290837e61adc4498e36f003aa3245ce).
Die Kamera streamt durchgehend das volle Sensorbild; das Zoomen besteht aus
Ausschnitt + Hochskalierung im Client, ohne Qualitätsgewinn über die Sensorauflösung
hinaus.

**Praktische Konsequenz:** Für ein eigenes Kontroll-Tool ist dafür **kein
HTTP-Request an die Kamera nötig**. Zoom lässt sich clientseitig nachbilden,
z. B. in Python/OpenCV: `frame[y1:y2, x1:x2]` gefolgt von `cv2.resize(...)`.

**Der RS485/Pelco-D-Codepfad unten ist mit hoher Wahrscheinlichkeit toter Legacy-Code**
aus der gemeinsamen JS-Bibliothek für echte mechanische PTZ-Kameras der D-Link-Serie.
Bei der DCS-2230 wird beim Loslassen des Zoom-Buttons zwar ein Stop-Befehl
(`rs485output=ff010000000001`) an die Kamera gesendet (mehrfach per Wireshark
bestätigt), der eigentliche Zoom-In/Out-Befehl (`ipncptz=...`) wurde dagegen in
keinem Mitschnitt beobachtet — konsistent mit der Erklärung, dass die visuelle
Zoom-Funktion diesen Codepfad gar nicht benötigt.

Format (für den Fall, dass der Pfad doch noch relevant wird): `FF` + Geräte-ID
(Default `01`) + 4 Befehlsbytes + Prüfsumme (Summe mod 256).

| Kommando | Hex-String | Status |
|---|---|---|
| `CMD_STOP` | `ff010000000001` | ✅ bestätigt (mehrfach, per `rs485output`) |
| `CMD_ZOOM_IN` | `ff010020000021` | 📄 berechnet aus `pelcod.js`, nie live beobachtet — vermutlich weil nicht benötigt |
| `CMD_ZOOM_OUT` | `ff010040000041` | 📄 berechnet aus `pelcod.js`, nie live beobachtet — vermutlich weil nicht benötigt |

## 4. Fokus / Blende / Belichtung

**Autofokus vermutlich nicht real vorhanden:** Nach Behebung des Auth-Bugs
(siehe Abschnitt 2) lieferte `autofocus=1` einen `502 Bad Gateway` mit der
Meldung "The CGI was not CGI/1.1 compliant" -- das deutet auf einen
abstürzenden/fehlerhaften CGI-Handler hin, nicht auf ein Auth-Problem. Passt
zur Festbrennweiten-Optik (4,37 mm, siehe Abschnitt 3) ohne dokumentiertes
Autofokus-Feature im offiziellen Datenblatt.

| Aktion | Request | Status |
|---|---|---|
| Autofokus auslösen | `GET /cgi-bin/lencontrol.cgi?autofocus=1` | ⚠️ `502 Bad Gateway` -- vermutlich nicht unterstützte Hardware |
| Autofokus-Region setzen | `GET /cgi-bin/lencontrol.cgi?autofocuslocation=<region>` | 📄 nicht getestet |
| Autofokus-Status abfragen | `GET /vb.htm?getautofocusbusy` | 📄 |
| Fokusposition abfragen | `GET /vb.htm?paratest=affocusposition` | 📄 |
| Blende | `GET /cgi-bin/aperture.cgi?...` | 📄 (Parameter nicht im Detail extrahiert) |
| Belichtung | `GET /cgi-bin/exposure.cgi?...` | 📄 |
| Verschlusszeit | `GET /cgi-bin/shutter.cgi?...` | 📄 |

## 5. Digitale Ein-/Ausgabe ✅

| Aktion | Request |
|---|---|
| Digitalausgang EIN | `GET /vb.htm?language=ie&giooutalwayson=1:1` |
| Digitalausgang AUS | `GET /vb.htm?language=ie&giooutalwayson=1:0` |
| Digitaleingang-Status | Teil des periodischen Pollings: `vb.htm?language=ie&getdlinkalarmstatus&getdlinksdstatus` |

## 6. IR-/Weißlicht-Steuerung 📄

| Aktion | Request |
|---|---|
| Licht ein | `GET /cgi-bin/light_ctrl.cgi?action=on` |
| Licht aus | `GET /cgi-bin/light_ctrl.cgi?action=off` |
| Helligkeit (0–100) | `GET /cgi-bin/light_ctrl.cgi?action=on&active=<0-100>` |
| Dämmerungsempfindlichkeit | `GET /cgi-bin/light_ctrl.cgi?sensitivity=0\|1\|2` |

## 7. Event-Verwaltung (`setup_event.htm`) 📄

Basis: `c_iniUrl` + Parameter, gesendet via `SendHttp(o, false)`.

| Aktion | Parameter |
|---|---|
| Event löschen | `&deleteevent=<id>` |
| Event-Medienregel löschen | `&deleteeventmedia=<id>` |
| Event-Aufnahmeregel löschen | `&deleteeventrecording=<id>` |
| Event-Serverregel löschen | `&deleteeventserver=<id>` |
| Event aktivieren/Flag | `&fj_event=<...>` |

## 8. Netzwerk & Konnektivität 📄

| Aktion | Request |
|---|---|
| Bonjour/mDNS | `cgi-bin/bonjour.cgi` |
| IPv6-Konfiguration | `cgi-bin/ipv6_config.cgi` |
| UPnP Portweiterleitung setzen | `cgi-bin/upnpportforwarding.cgi` |
| UPnP Portweiterleitung testen | `cgi-bin/upnpportforwardingtest.cgi` + `upnpportforwardingteststatus.cgi` |
| DDNS konfigurieren | `cgi-bin/ddns.cgi` — Felder: `&account=`, `&hostname=`, `&interval=`, `&password=`, `&provider=`, `&servername=` |
| WLAN scannen | `cgi-bin/wifi_scan.cgi` |
| WLAN-Konfiguration setzen | `cgi-bin/wifi_config.cgi` |
| WLAN-Zertifikat hochladen (WPA-Enterprise) | `POST cgi-bin/wifi_upload.cgi` (multipart/form-data) |

## 9. HTTPS / Zertifikate 📄

| Aktion | Request |
|---|---|
| HTTPS-Einstellungen speichern | `POST /cgi-bin/https.cgi` |
| SSL-Zertifikat hochladen | `POST /cgi-bin/sslcert.cgi` (multipart/form-data) |
| Zertifikat exportieren | `GET /cgi-bin/exportcert.cgi` |
| CSR exportieren | `GET /cgi-bin/exportcsr.cgi` |

## 10. SNMP 📄

| Aktion | Request |
|---|---|
| SNMP-Einstellungen | `cgi-bin/snmp.cgi` — Felder u.a. `&snmp_v3_enable=`, `&snmp_v3_ro_authtype=`, `&snmp_v3_rw_authpass=` |

## 11. Event-Benachrichtigung (HTTP) 📄

| Aktion | Request |
|---|---|
| HTTP-Notification-Ziel konfigurieren | `cgi-bin/event_http.cgi` |

## 12. Wartung / System 📄

| Aktion | Request |
|---|---|
| Konfiguration exportieren | `GET /cgi-bin/exportconf.cgi` |
| Konfiguration importieren | `POST /cgi-bin/loadconf.cgi` (multipart/form-data) |
| Geplanter Neustart | `cgi-bin/timedreboot.cgi` |
| Werksreset (PTZ-Mechanik) | `cgi-bin/longcctvreset.cgi` |
| Firmware-Update | `POST /update.cgi` (multipart/form-data, **kein** `/cgi-bin/`-Präfix!) |
| Benutzer hinzufügen | `cgi-bin/adduser.cgi` |
| Base64-Hilfsfunktion (Zweck unklar) | `cgi-bin/base64.cgi` |
| Prüfsumme berechnen | `cgi-bin/checksum.cgi` |

## 13. Log / Status 📄

| Aktion | Request |
|---|---|
| Systemlog abrufen (mit Paginierung) | `GET /cgi-bin/systemlog.cgi?eventstart=<startindex>` |
| Log löschen | `GET /cgi-bin/clearlog.cgi` |
| Log exportieren | `GET /cgi-bin/exportlog.cgi` (per Browser-Navigation, `window.location=...`) |
| Log-Daten (v2-Format, vermutlich AJAX-Polling) | `GET /cgi-bin/logdata.cgi` |
| Remote-Syslog-Server konfigurieren | `GET /cgi-bin/remotelog.cgi?enable=<0/1>&server=<host>&port=<port, Default 514>` |

**Hinweis zu `status_info.htm`:** Reine Read-only-Anzeigeseite (Firmware-Version, MAC,
IP, Subnetz, Gateway usw.), enthält keine Requests/Aktionen — daher nicht als eigener
Abschnitt gelistet.

## 14. Snapshot / Bildabruf ✅ (Endpunktname per Wireshark bestätigt)

| Aktion | Request |
|---|---|
| Snapshot Profil 1 (1920×1080 in deinem Mitschnitt) | `GET /dms?nowprofileid=1&<cache-buster>` |
| Snapshot Profil 3 (640×360 in deinem Mitschnitt) | `GET /dms?nowprofileid=3&<cache-buster>` |

## 15. Sprache 📄

| Aktion | Request |
|---|---|
| UI-Sprache setzen | `GET /cgi-bin/setmultilanguage.cgi?mui=<code>` |

---

## 16. RTSP-Streaming-Profile

| Profil | Auflösung (Default) | RTSP verfügbar? |
|---|---|---|
| 1 | 1920×1080 | ✅ bestätigt (`rtsp://IP:554//live1.sdp`) |
| 2 | 640×360 | ✅ bestätigt (`rtsp://IP:554//live2.sdp`) |
| 3 | 640×360 | ❌ **bestätigt NICHT verfügbar** — `rtsp://IP:554//live3.sdp` liefert RTSP-Session-Setup-Fehler (VLC: "Failed to connect"). Für Snapshots (`dms?nowprofileid=3`) funktioniert Profil 3 aber (siehe Abschnitt 14). Da Profil 1 und 2 beide bestätigt funktionieren, Profil 3 nicht: Die Kamera bietet vermutlich nur 2 gleichzeitige RTSP-H.264-Streams an, Profil 3 ist nur für Standbild/MJPEG gedacht.

## ⚠️ Nicht relevant für dieses Modell

Folgende Endpunkte sind Teil derselben JS-Codebasis, aber laut `var.js`-Flags
(`g_isSupportVisca=0`, `g_supportFishEye=0`, `g_supportZbc=0`, `g_support_real_ptz=0`,
`g_isSupportRS485=0`) bei der DCS-2230 **inaktiv** — nur zur Vollständigkeit gelistet,
falls du das Skript auf ein anderes D-Link-Modell anwendest:

- Visca-Protokoll: `visca.cgi`, `viscapreset.cgi`, `viscahome.cgi`
- Fisheye-Funktionen: `fisheye.cgi`, `fisheyespeed.cgi`
- Zbc-Speed-Dome: `zbcautopan.cgi`, `zbchome.cgi`, `zbcpreset.cgi`, `zbcsequence.cgi`,
  `zbcMousewheel.cgi`, `zbcmousedrag.cgi`, `zbcreboot.cgi`
- "Echtes" mechanisches PTZ (Long-CCTV-Serie): `longcctvapn.cgi`, `longcctvhome.cgi`,
  `longcctvmove.cgi`, `longcctvmovemode.cgi`, `longcctvpst.cgi`, `longcctvseq.cgi`,
  `longcctvspdstp.cgi`, `longcctvcalibrate.cgi`, `longcctvmounttype.cgi`

---

## Offene Punkte / nicht verifiziert

1. **Vollständige Parameterlisten** der reinen Konfigurationsformulare (Netzwerk-IP-Einstellungen,
   Bildparameter wie Helligkeit/Kontrast, Zeitplan-Strings) wurden nicht Feld-für-Feld extrahiert —
   diese Seiten bauen ihre Parameter über `.GV()`-Objektmethoden zusammen, nicht als literale Strings,
   was eine einfache Textsuche erschwert. Bei Bedarf kann das gezielt für einzelne Seiten nachgeholt werden.
2. **HTTPS-Feldnamen, SNMP-Feldnamen im Detail, WLAN-Konfigurationsfelder** wurden nur als
   Ziel-Endpunkt identifiziert, nicht mit vollständigen Parameternamen.
3. **`base64.cgi` und `myappro.cgi`** — Zweck aus dem Kontext nicht eindeutig erkennbar,
   nicht weiter untersucht.

~~Zoom-Hex-Werte für IN/OUT~~ — **geklärt, siehe Abschnitt 3**: Zoom ist reines
Client-seitiges Software-Zoom, die Kamera-Kommandos werden dafür nicht benötigt.
