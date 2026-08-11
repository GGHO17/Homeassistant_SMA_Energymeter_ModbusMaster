# SMA Meter Simulator für Home Assistant

Custom Integration, die aus beliebigen Quellen (Modbus TCP, MQTT) ein
**SMA-Energy-Meter-Telegramm** erzeugt und per UDP-Multicast versendet.
Damit lässt sich ein SMA Energy Meter bzw. Sunny Home Manager 2.0 gegenüber
SMA-Wechselrichtern nachbilden, ohne die Hardware zu betreiben.

Erste Datenquelle: **PQI-DA smart** über Modbus TCP.

## Status

| Baustein | Stand |
|---|---|
| Telegramm-Encoder (608 Byte, Protokoll-ID 0x6069) | steht, gegen Wireshark zu verifizieren |
| Multicast-Sender mit Interface-Auswahl | steht |
| Modbus-Quelle mit Blocklesen und fester Taktung | steht, Registerkarte offen |
| MQTT-Quelle (HA-Broker oder eigener Broker) | steht |
| Geräteprofile als YAML, beliebig erweiterbar | steht |
| Profil Shelly Pro 3EM | steht, aus offizieller Doku |
| Profil PQI-DA smart (Schnellblock 10 ms) | steht, aus offizieller Datenpunktliste |
| Register-Scanner (`tools/scan_modbus.py`) | steht |
| Mehrere Quellen parallel, je eigenes Intervall | steht |
| Glättung + Energieintegration + Persistenz | steht |
| Config Flow, Diagnose-Sensoren, HACS-Metadaten | steht |
| Unicast-/„direkte Zählerkommunikation" | offen |

## Protokoll

- Multicast **239.12.255.254**, Port **9522**
- SMA-Net-Protokoll-ID **0x6069** („Energy Meter Protocol", 608 Byte)
- SusyID: **349** = Energy Meter 2.0, **372** = Sunny Home Manager 2.0
- Skalierung: Leistung 0,1 W · Energie Ws · Strom mA · Spannung mV ·
  cos φ und Frequenz 0,001
- Referenz: SMA TI „SMA ENERGY METER Zählerprotokoll" (EMETER-Protokoll-TI, cdn.sma.de)

Wichtig: Der Wechselrichter muss auf **genau diese Seriennummer** konfiguriert
werden, und es darf kein zweites Gerät mit derselben Seriennummer senden.

## Schnelltest ohne Home Assistant

```bash
python3 tools/send_test.py --dump-only            # Hexdump des Telegramms
python3 tools/send_test.py --interval 1.0         # Lastprofil senden
tshark -i eth0 -f "udp port 9522" -x              # mitschneiden
```

Gegenprobe: eine beliebige Energy-Meter-Implementierung (z. B. `sma-em`,
Node-RED, iobroker) als Empfänger laufen lassen – wird der simulierte Zähler
erkannt und plausibel dekodiert, stimmt das Telegramm.

## Quellen konfigurieren

Der Einrichtungsdialog führt über ein Menü: beliebig viele Quellen hinzufügen,
je Quelle **Modbus** oder **MQTT**, jederzeit über *Konfigurieren* änderbar.

**Modbus**: Host, Port, Unit-ID, Abfrageintervall, Gerätprofil.
Zusätzlich zwei Schalter für die typischen Stolperfallen: *Wortreihenfolge
tauschen* und *Vorzeichen umkehren* (falls das Gerät Einspeisung positiv zählt).

**MQTT**: standardmäßig der Broker der HA-MQTT-Integration; alternativ ein
eigener Broker mit Host, Port und Zugangsdaten. Topics werden einzeln
zugeordnet, bei JSON-Payload über einen Pfad wie `power.total`.

Bei beiden lässt sich statt eines Profils *Eigene Zuordnung (manuell)* wählen –
dann fragt der Dialog Register bzw. Topics nacheinander ab.

### Eigene Geräte ergänzen

Geräte werden über YAML-Profile beschrieben, nicht über Code:

```
custom_components/sma_meter_sim/device_profiles/   mitgeliefert
<config>/sma_meter_sim_profiles/                   eigene, updatefest
```

`device_profiles/_template.yaml` als Vorlage kopieren, ausfüllen, Integration
neu laden – das Profil steht im Dialog zur Auswahl. Mitgeliefert sind aktuell
`pqi_da_smart`, `generic_modbus_minimal` und `generic_mqtt_json`.

Minimal reicht ein einziger Wert: Aus der Summenwirkleistung bildet die
Integration Bezug/Lieferung, Scheinleistung und die Energiezähler selbst.

## Vorzeichen und Richtung

Das Telegramm hat **je Richtung ein eigenes Feld** (Bezug und Lieferung getrennt),
Messgeräte wie das PQI-DA smart liefern dagegen **einen vorzeichenbehafteten Wert**.
Die Integration übernimmt die Aufteilung: positiver Wert → Bezug, negativer →
Lieferung. Erwartet wird also die Konvention *positiv = Bezug aus dem Netz*; zählt
das Gerät umgekehrt, gibt es im Dialog den Schalter *Vorzeichen umkehren*.

## Register herausfinden

Für Geräte ohne fertiges Profil liegt ein Scanner bei:

```bash
pip install pymodbus
python3 tools/scan_modbus.py 192.168.1.50 --start 0 --end 2000
python3 tools/scan_modbus.py 192.168.1.50 --watch 1013     # eine Adresse beobachten
```

Der Scan zeigt nur Werte, die als Spannung, Strom, Leistung oder Frequenz
plausibel sind, und deutet jedes Registerpaar in beiden Wortreihenfolgen.
Mit `--watch` einen großen Verbraucher schalten – welcher Wert passend
mitgeht, ist das gesuchte Register.

## Installation

1. Repository bei GitHub anlegen, Inhalt pushen, Release mit Tag `0.1.0` erzeugen
2. In HACS als *Custom Repository* (Kategorie: Integration) hinzufügen
3. Installieren, Home Assistant neu starten, Integration hinzufügen

## Netzwerk (HAOS als VM unter Proxmox)

- HAOS nutzt das Host-Netz der VM, Multicast funktioniert grundsätzlich direkt.
- Bei mehreren Interfaces oder VLANs die **Interface-IP** im Config Flow setzen
  (`IP_MULTICAST_IF`), sonst geht das Telegramm ggf. über das falsche Interface raus.
- TTL 1 reicht, solange Wechselrichter und HA im selben Subnetz liegen.
- Auf der Proxmox-Bridge kann IGMP-Snooping Multicast verschlucken. Falls nichts
  ankommt: `bridge link` bzw. `multicast_querier`/`multicast_snooping` der Bridge prüfen.

## Offene Punkte

1. **PQI-DA smart am Gerät verifizieren** – die Adressen stammen aus der
   offiziellen Datenpunktliste, drei Dinge sind aber gerätabhängig: Input- oder
   Holding-Register, Adressbasis (0- oder 1-basiert) und die Vorzeichenrichtung.
   Details stehen im Profil; bei Abweichungen hilft `tools/scan_modbus.py`.
2. **Abtastung**: Ein Modbus-Poll liefert den aktuellen Wert, keinen Puffer.
   Echte 100-ms-Mittelwerte aus 10-ms-Daten erfordern 100 Polls/s; realistischer
   ist ein Poll-Intervall von 100 ms mit Glättung. Der Zähler `overruns` in
   den Diagnoseattributen zeigt, ob der Takt gehalten wird.
3. **Regelgeschwindigkeit**: Der begrenzende Faktor ist der Wechselrichter, nicht
   die Messung – er verarbeitet Zählerdaten in seinem eigenen Takt und hat danach
   noch seine Batterierampe. Die schnelle Erfassung senkt vor allem die Totzeit
   (frischer Mittelwert statt alter Momentaufnahme). Ob der Wechselrichter auch
   Telegramme schneller als 1/s annimmt, ist per Versuch zu klären.
4. **Energiezähler**: Werden derzeit aus der Leistung integriert und alle 60 s
   persistiert. Falls der PQI Energieregister liefert, diese direkt übernehmen –
   dann stimmen die Zählerstände auch nach Ausfällen.
5. **Direkte Zählerkommunikation**: Neuere SMA-Wechselrichter fragen den Zähler
   teilweise per Unicast an, statt nur Multicast mitzuhören. Sobald der
   Hybrid-WR da ist: Mitschnitt anfertigen und ggf. eine Antwortlogik ergänzen.
6. Discovery-Request (`534d4100000402a0ffffffff0000002000000000`) beantworten,
   damit der Simulator vom Suchlauf gefunden wird.

## Struktur

```
custom_components/sma_meter_sim/
├── __init__.py          Setup/Teardown der Integration
├── config_flow.py       Einrichtung über die Oberfläche
├── const.py             Konstanten
├── coordinator.py       Orchestrierung, Sendetakt, Persistenz
├── pipeline.py          Glättung, Import/Export-Split, Energieintegration
├── factory.py           baut Quellen aus der Konfiguration
├── profiles.py          Lader für die YAML-Geräteprofile
├── device_profiles/     mitgelieferte Profile + Vorlage
├── sensor.py            Diagnose-Entities
├── speedwire.py         Telegramm-Encoder + Multicast-Sender
└── sources/             Modbus-, MQTT-Quellen
```
