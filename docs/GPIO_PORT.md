# Experimenteller GPIO-Port für Samsung GPI1

Der erste Port ergänzt `VoodooGPIO.kext` um die eigene Klasse `VoodooGPIOSamsungLunarLake`. Sie verwendet die vorhandene VoodooGPIO-Schnittstelle und besitzt eine eigene WorkLoop, CommandGate, IRQ-Zustellung und Power-Behandlung. Die älteren Upstream-Plattformklassen bleiben erhalten. GPIO wird weiterhin getrennt von I2C-Bus, HID-Satellit, VoodooInput und GPU-Treiber gebaut.

Die neue Personality enthält `LunarBridgeGPIOEnabled = false`. Ohne ausdrückliche Aktivierung beendet sich ihr `start()` vor ACPI-Auswertung, PCI-Prüfung, MMIO-Mapping und IRQ-Registrierung. Auch mit Aktivierung akzeptiert die Klasse ausschließlich den unten beschriebenen Samsung-GPI1-Aufbau. Das beigefügte OpenCore-Fragment bleibt ebenfalls vollständig deaktiviert. Die Buildskripte schreiben keine EFI, ändern keinen Datenträger und ändern keine Firmware-Secure-Boot-Policy.

## Nachgewiesene Quell- und Firmwarebeschreibung

Die lokal gelesene ACPI-Tabelle des Samsung Galaxy Book5 Pro NP960XHA-KG3DE beschreibt GPI1 mit `_UID = 1`, `_CID = INTC105F` und dem über `GHID()` gewählten `_HID` INTC105D. Firmware-Variablen können Status und HID-Zweig ändern. Der Quelltextnachweis ersetzt keine Auswertung dieser Methoden unter macOS. Die vollständige lokale Firmwaretabelle wird nicht in das Repository oder Buildpaket übernommen.

Zusätzlich zur ACPI-Beschreibung verlangt der Port einen veröffentlichten Intel-I2C-PCI-Anbieter mit `8086:A878`–`8086:A87B` und `144D:C1DD`. Fehlt dieser Anbieter beim Start, wird der Start abgelehnt. Ob GPIO und I2C auf dem Zielsystem in passender Reihenfolge veröffentlicht werden, ist noch zu testen.

| GPI1-Eigenschaft | Akzeptierter Wert |
| --- | --- |
| Community / Softwarebeschreibung | `Community1` / `0x00010000` |
| Gruppenanzahl / Konfigurationsabstand | `3` / `0x10` |
| Pad-Besitz / Konfigurationssperre | `0xD0` / `0x110` |
| Host-Software-Besitz | `0x130` |
| Interrupt-Status / Interrupt-Enable | `0x200` / `0x210` |
| Einzige MMIO-Ressource | `0x10000` Byte; DWORD-ausgerichtete virtuelle Adresse |
| Zusätzliches Register-Gate | PADBAR bei Offset `0x0C` muss `0x600` ergeben |

| Gruppe | Community-Pins | Pad-Anzahl | Konfiguration ab | IRQ-Freigabe |
| --- | --- | --- | --- | --- |
| GPP_F / GPPF | 0–25 | 26 | `0x600` | Nur nach einzelnen Besitz-/Sperr-/Mux-Prüfungen |
| GPP_E / GPPE | 26–50 | 25 | `0x7A0` | Nur nach einzelnen Besitz-/Sperr-/Mux-Prüfungen |
| CPUJTAG / JTAG | 51–64 | 14 | `0x930` | Ausgeschlossen |

Die Firmware beschreibt den Touchpad-Zweig für `TPDI = 0` mit `GNUM(0x00180492)` und `ResourceSource = "\\_SB.GPI1"`. Die lokale Aufteilung ergibt Community 1, Gruppe 1, Pad 18: **Community-Pin 44**. Der alternative Firmwarezweig verwendet APIC. Der aktive Zweig, die tatsächliche `_CRS`/`_DSM`-Auswahl und der Laufzeit-Pin sind noch nicht unter macOS ausgewertet.

Für Pin 44 berechnet das geprüfte Modell Pad-Konfiguration `0x8C0`, PADOWN `0xE8` mit Besitzfeld ab Bit 8, Konfigurationssperre `0x118`, Host-Besitz `0x134`, IRQ-Status `0x204` und IRQ-Enable `0x214`. Diese Werte stammen aus der ACPI-Beschreibung und dem Modell; die Register wurden auf dem Windows-Gerät weder geschrieben noch zur Funktionsprüfung ausgelesen.

Der [Intel-Plattform-GPIO-Treiber am gepinnten Linux-Stand](https://github.com/torvalds/linux/blob/e5f0a698b34ed76002dc5cff3804a61c80233a7a/drivers/pinctrl/intel/pinctrl-intel-platform.c) matcht INTC105F und liest Ownership-, Lock-, Host-Ownership-, Status-/Enable-Eigenschaften aus `_DSD`. Der [gemeinsame Intel-GPIO-Treiber desselben Stands](https://github.com/torvalds/linux/blob/e5f0a698b34ed76002dc5cff3804a61c80233a7a/drivers/pinctrl/intel/pinctrl-intel.c) erklärt die PADOWN- und Interrupt-Feldanordnung. Beide GPLv2-Quellen wurden als Referenz geprüft; es wurde keine Linux-Implementierung in diesen GPLv3-Port kopiert. Originale VoodooGPIO-/VoodooI2C-Lizenzen und Copyrights bleiben erhalten.

## Besitz, Bounds und Interrupt-Zuordnung

Die Klasse prüft das vollständige feste Profil, bevor sie Registeradressen berechnet. Pin-Nummer, Gruppenindex, MMIO-Grenzen und DWORD-Ausrichtung werden geprüft. Unpassende oder fehlende `_DSD`-Werte, doppelte angeforderte Eigenschaften, fremde PCI-Identitäten, abweichende Fenstergrößen und fehlender PADBAR-Nachweis verhindern den Start. Andere Communities werden in diesem ersten Port nicht implementiert.

Eine Pin-Registrierung verlangt Host-Besitz, ein Null-PADOWN-Feld, entsperrte Konfiguration, GPIO-Mux, aktivierten Eingang, deaktivierten Ausgang sowie keine APIC-/SCI-/SMI-/NMI-Route. Ein bereits aktives IRQ-Enable-Bit wird nicht übernommen. PADOWN, Host-Besitz und Lock-Register werden nie geschrieben. Der Treiber ruft keine Firmwaremethode zur Übernahme eines Pins auf.

`enableInterrupt()` verändert ausschließlich die Ereignis-/Invertierungsfelder des zugeordneten Pads und sein eigenes Status-/Enable-Bit. Die fünf unterstützten IRQ-Arten sind steigende Flanke, fallende Flanke, beide Flanken, High-Level und Low-Level. Andere Konfigurationsfelder bleiben erhalten. Readback und erneute Besitzprüfungen begrenzen die Fehlerbehandlung. Nach einer beobachteten Änderung von Besitz oder Mux hält die Klasse die IRQ-Zustellung an und nimmt bis zum Abbau der Instanz keine weiteren Client-Kommandos an. Aufräumen stellt ein eigenes Konfigurationswort nur dann zurück, wenn es noch exakt dem programmierten Wert entspricht und Besitz-/Sperrprüfungen weiterhin bestehen.

Bei Sleep maskiert der Port nur seine eigenen aktiven Pins. Nach Wake müssen PADBAR, Besitz, Mux, Sperren und Konfiguration weiterhin passen, bevor einzelne vorher aktive Pins wieder freigegeben werden. Es gibt keinen globalen Community-Reset und keine pauschale Wiederherstellung von Firmware-Pin-Besitz. Diese Quellpfade sind noch nicht auf echter Hardware ausgeführt worden.

Bei der IRQ-Zustellung halten Referenzen den Controller während `dispatch()` und die eigene EventSource während ihres gesamten `checkForWork()`-Aufrufs fest. Nach jedem Client-Callback und vor jeder weiteren Piniteration prüft der Port Bereitschaft, Wake-Zustand, Registeradresse und Mapping neu. Eine durch den Callback veränderte Registrierung wird danach nicht wiederverwendet. Das verhindert den erkannten Weiterlauf nach reentrantem Close oder Sleep; konkurrierende Kernel-Lebensdauerpfade müssen weiterhin auf dem Ziel getestet werden.

Die [IOKit-Primärimplementierung](https://github.com/apple-oss-distributions/xnu/blob/main/iokit/Kernel/IOInterruptEventSource.cpp) verwendet EventSource-Felder auch nach Rückkehr aus der Client-Aktion. Deshalb umfasst der zusätzliche EventSource-Retain die gesamte Basismethode. `getInterruptType()` liefert wie VoodooGPIOIntel den IOKit-Typ der Parent-Zustellung; die getrennten Linux-IRQ-Typwerte programmieren ausschließlich das Pad. Bei Besitzverlust wird die eigene Parent-Zustellung deaktiviert, ohne Status-/Enable-Bits des inzwischen fremden Pins zu schreiben. Das reale Sharing-Verhalten bleibt ein Hardware-Abnahmepunkt.

Der ACPI-Ressourcenleser ist jetzt iterativ und begrenzt statt rekursiv. Er akzeptiert nur vollständig begrenzte Einzel-Pin-GPIO-Deskriptoren mit gültigem ResourceSource-String und verwirft abgeschnittene Daten, ungültige Offsets, reservierte Polarität und die ungültige Kombination Level/Both. Die bestehende Flankenpolarität wurde korrigiert: ActiveHigh ergibt Rising, ActiveLow Falling. Für die eng gematchten Samsung-I2C-Anbieter muss der GPIO-Dienst dieselbe `LunarBridgeGPIOResourceSource` wie die tatsächlich ausgewählte Client-Ressource besitzen. Ein beliebiger zuerst gefundener GPIO-Anbieter wird dafür nicht verwendet.

## Nachvollziehbare Diagnose

Die Klasse veröffentlicht `LunarBridgeGPIOResourceSource`, `LunarBridgeGPIOExperimental`, `LunarBridgeGPIOPins` und den gelesenen `LunarBridgeGPIORevision`-Wert in der IORegistry. Nach Registrierung kommen `LunarBridgeGPIOLastPin` und `LunarBridgeGPIOLastType` hinzu. `LunarBridgeGPIOOwnershipFaults` wird bei erkanntem Besitz-/Mux-Verlust aktualisiert; `LunarBridgeGPIOIRQCallbacks` zählt zugestellte Callbacks und wird alle 64 Zustellungen sowie beim Aufräumen veröffentlicht. Dieser Wert ist daher kein kontinuierlich aktualisierter Echtzeit-Zähler.

Kernel-Logs beginnen mit `LunarBridgeGPIO:` und nennen deaktivierten Start, erfolgreichen experimentellen Start, Pin-/IRQ-Art bei Registrierung, verweigertes Resume und gestoppte IRQ-Zustellung nach Besitz-/Mux-Änderung. Der I2C-Nub veröffentlicht die ausgewählte `gpioResourceSource`. Diese Diagnosen benötigen einen tatsächlich geladenen Zieltreiber; es liegen noch keine solchen Laufzeit-Traces vor.

## Reproduktion und überprüfter Stand

`i2c-patch/prepare_build.py` erstellt aus den gepinnten Quellen einen neuen Snapshot. Nach den bestehenden Xcode-/Versions-/Bibliothekspfad-Anpassungen wendet es `gpio-patch/prepare_gpio.py` an. Die zusätzlichen sechs Eingangshashes, vier eigenen Quellhashes und der exakte generierte Unified-Diff werden vor jeder Änderung kontrolliert. **Der GPIO-Diff setzt diesen vorbereiteten Basisstand voraus; er ist kein Patch für einen beliebigen unveränderten Upstream-Checkout.** Der Vorbereitungsbericht enthält die Vorher-/Nachherhashes aller zehn betroffenen Dateien.

Für lokale oder native Host-Prüfungen:

```sh
python3 i2c-patch/test_build.py --prepared build/i2c-prepared
python3 gpio-patch/test_gpio.py --prepared build/i2c-prepared --compiler clang++
python3 i2c-patch/build_i2c.py --prepared build/i2c-prepared --output build/i2c-native
```

Die acht GPIO-Tests kompilieren und führen das tatsächliche C++-Bounds-/Ownership-/IRQ-Modell sowie den vorbereiteten ACPI-Ressourcenleser aus. Der Hostadapter entfernt nur dessen unbenutzten Kernel-Include; er ersetzt keine Kernel-API durch Stubs. Geprüft werden der vollständige PCI-Geräte-ID-Bereich, alle 65 Pins, Pin-44-Adressen, Ownership-/Lock-/Mux-Fälle, alle IRQ-Arten, alle Kürzungen des beobachteten 35-Byte-Deskriptors, ungültige Offsets und ein 64-KiB-Ressourcenstrom ohne rekursiven Stack-Aufbau. Weitere Tests kontrollieren deaktivierte Personality, Quellhashes, die tatsächliche Xcode-Quellphase und die ausgewählte ResourceSource. Eine zusätzliche strukturelle Negativregression lehnt sechs gezielt veränderte Quellvarianten ab: fehlende Zustands-Gates, fehlende Controller-/EventSource-Retains, Schreiben fremder Bits nach Besitzverlust und eine Verwechslung der Trigger-API. Dieser Test führt keine IOKit-Callbacks aus.

Die Paketprüfung verwendet ausdrücklich synthetische, nicht ausführbare Mach-O-Fixtures. Der anschließende native Build muss dagegen echte x86_64-`MH_KEXT_BUNDLE`-Dateien mit exakten Modul-/Plist-Metadaten und einem definierten Symbol der neuen GPIO-Klasse liefern. Ein Plist-Match ohne kompilierten Treibercode genügt der Prüfung nicht. Das Paket enthält eigene GPIO-Quellen, Patch, Tests, beide Dokumentationen und originale Upstream-/SDK-Lizenztexte samt Herkunft und SHA-256-Werten.

Alle acht bestehenden PCI-/Source-Tests, 21 Vorbereitungs-/Pakettests und acht neuen GPIO-Tests bestehen lokal. Die aktuelle GPIO-Klasse, der vorbereitete Ressourcenleser und das I2C-Gerätenub wurden zusätzlich mit C++11 gegen die echten gepinnten SDK-Header als drei x86_64-Mach-O-Objekte kompiliert. Der generierte Patch besteht `git apply --check` auf seinem kontrollierten Basisstand. Diese Prüfungen führen keine Registerzugriffe aus und laden kein Kext.

Der [native GPIO-Lauf 38110502047](https://github.com/IT-EXPRESS-Bayern/LunarBridge-I2C/actions/runs/38110502047) am Quellstand `dba4c2192745e6463a9b794bdb3a45eaf4ac0bc9` ist erfolgreich und enthält fünf echte getrennte Kexts einschließlich der neuen Samsung-Klasse. Im heruntergeladenen GPIO-Binärprogramm ist das kompilierte Klassensymbol vorhanden; seine Personality ist deaktiviert. Eine unabhängige Paketprüfung bestand 48 Struktur-/Hash-/Metadatenprüfungen. Alle 195 Sourcehashes wurden geprüft: 172 Upstream-Einträge stimmen mit dem unabhängig vorbereiteten Quellstand überein, 23 eigene Projektdateien mit dem exakten Build-Commit. Das bestätigt den nativen Link und Paketinhalt, jedoch kein am Samsung geladenes Kext und keine GPIO-/IRQ-Funktion.

## Noch fehlende native Hardwarebelege

1. Tatsächlich ausgewertete `_HID`, `_CID`, `_UID`, `_STA`, `_DSD`, `_CRS` und `_DSM` des Zielsystems; GPI1-Ressourcen, Eltern-IRQ und der gewählte Touchpad-Interruptzweig.
2. Veröffentlichungsreihenfolge des Samsung-PCI-Anbieters und GPI1-Diensts; erfolgreicher enger Match, tatsächliches PADBAR/REVID sowie Pin-Besitz, Sperren, Eingang, Mux und Route. Firmwareänderungen werden nicht durch Lockerung der Gates umgangen.
3. Die gewünschte ResourceSource-/Pin-Zuordnung in IORegistry, erfolgreiches IRQ-Register/Enable/Disable/Unregister und messbare HID-Reports. Parent-IRQ-Sharing, ACK-Reihenfolge und Level-/Flankenverhalten müssen dabei bestätigt werden.
4. Tatsächliche IMG4100-Touchpad-Eingabe, Fehlerpfade und Last; GXTP7936-Touchscreen/APIC und andere Communities werden separat qualifiziert.
5. Sleep/Wake mit fortgesetzter Eingabe, gleichzeitige IRQ-/Client-/Power-/Stop-Aufrufe und sichere Dienst-/Client-Freigabe. Besitz-/Mux-Änderungen, abgelehnte Readbacks und unerwartete Ressourcen müssen kontrolliert geprüft werden.
6. Der eigenständig qualifizierte native Boot- und Firmware-UEFI-Secure-Boot-Pfad. Das Paket ist unsigniert; ein erfolgreicher Build bestätigt keine Vertrauenskette und verändert keine Policy.

Bis diese Nachweise vorliegen, bleibt die Personality experimentell und deaktiviert. Es wird weder Touchpad-/Touchscreen-Funktion noch störungsfreies Resume behauptet.
