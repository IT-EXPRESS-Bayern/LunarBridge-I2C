# Eigenständiger I2C-Build für Samsung Lunar Lake

Dieses Projekt baut den I2C-Controller und seine Eingabe-Abhängigkeiten getrennt vom GPU-Treiber. Die Paketierung enthält einzelne Kexts und keine Plug-ins innerhalb von LunarBridge.kext. Die Controller-Erweiterung ist ein experimenteller Quellpatch; noch kein Kext wurde auf dem Samsung-Laptop unter macOS Tahoe geladen.

## Bausteine und Herkunft

| Eigenständiges Bundle | Zweck | Quellstand |
| --- | --- | --- |
| VoodooGPIO.kext | Unveränderte GPIO-Schnittstelle/ältere unterstützte Plattformen; noch keine Lunar-Lake-GPIO-Implementierung | VoodooI2C/VoodooGPIO, `733dc8063f5ef07288a7bc648fff5348df6d8f65` |
| VoodooI2CServices.kext | Upstream-Dienste und vorhandene Protokollierung | Teil von VoodooI2C, `5c0c99cc451b03790c684ce6552c3f61620ad46b` |
| VoodooI2C.kext | I2C-Bus, ACPI-Gerätenubs und Samsung-PCI-Matching | VoodooI2C/VoodooI2C, `5c0c99cc451b03790c684ce6552c3f61620ad46b` plus dieses Projekts Samsung-Patch |
| VoodooI2CHID.kext | HID-over-I2C für Touchpad/Touchscreen über vorhandene HID-Schnittstellen | VoodooI2C/VoodooI2CHID, `27277936bc16c215ae7f9b31e76e3231778d04bd` |
| VoodooInput.kext | Eingabe-Aggregator für die Upstream-Multitouch-Engine | acidanthera/VoodooInput, `d897813718c2ef9ad143b6a86240fe52dcb1693e` |

Der [Upstream-Aufbau von VoodooI2C](https://github.com/VoodooI2C/VoodooI2C/blob/5c0c99cc451b03790c684ce6552c3f61620ad46b/README.md) trennt Bus und Satelliten schon auf Quellcode-Ebene. Dieses Projekt entfernt zusätzlich die aktive Plug-in-Paketierung im gepinnten Xcode-Projekt und baut die fünf Zielprojekte einzeln. Die üblichen Abhängigkeiten zwischen diesen I2C-Komponenten bleiben erhalten; keine Komponente benötigt den GPU-Kext.

## Controller-Matching und Firmware-HID

Der neue PCI-Personality-Eintrag akzeptiert exakt `8086:A878`, `A879`, `A87A` und `A87B` mit Samsung-Subsystem `144D:C1DD`. Alle bisherigen Personality-Einträge bleiben erhalten. Die verwendeten 100-MHz-Controller-Takt- und 42/171/208-ns-Timing-Werte stammen aus dem bereits geprüften Linux-LPSS-Descriptor `ehl_i2c_info`; damit ist noch keine funktionierende Register-, Power- oder Interrupt-Behandlung auf dem Zielgerät bewiesen.

Die lokal gelesene Samsung-DSDT beschreibt `IMG4100` an `_SB.PC00.I2C0.SPTP` und `GXTP7936` an `_SB.PC00.I2C1.STSP`. Beide besitzen `PNP0C50` als kompatible HID-over-I2C-Kennung. Ihre HID-Descriptor-Register und I2C-Adressen kommen aus den tatsächlichen ACPI-Methoden; statische DSDT-Zweige ersetzen keine Auswertung von `_CRS` und `_DSM` unter macOS.

Der Touchpad-Zweig nutzt abhängig von Firmware-Variablen GPIO an `_SB.GPI1` oder APIC. Der Touchscreen-Zweig beschreibt APIC und eine Power-Ressource. Die GPIO-Geräte erhalten `INTC105D` durch `GHID()` und besitzen `INTC105F` als `_CID`; `GPHD` kann den `_HID`-Zweig ändern. Die [gepinnten GPIO-Matches](https://github.com/VoodooI2C/VoodooGPIO/blob/733dc8063f5ef07288a7bc648fff5348df6d8f65/VoodooGPIO/Info.plist) enthalten diese Kennungen nicht. Sie werden hier nicht auf ältere Plattformen umbenannt.

Die DSDT enthält für Lunar Lake eigene Community-Gruppen und Register-Offsets, zum Beispiel Interrupt-Status `0x200` und Interrupt-Enable `0x210`. Die älteren GPIO-Plattformdaten haben andere Belegungen. Ein echter GPIO-Port benötigt deshalb Community-, Pin-, Ownership- und Interrupt-Daten sowie Laufzeitprüfungen auf dem Laptop. Nur neue ACPI-Matches wären unzureichend.

Ein konkreter Vergleichspunkt ist der [Intel-Plattform-GPIO-Treiber in Linux v6.17](https://github.com/torvalds/linux/blob/e5f0a698b34ed76002dc5cff3804a61c80233a7a/drivers/pinctrl/intel/pinctrl-intel-platform.c). Er matcht `INTC105F` und liest die gleichen `intc-gpio-*-`Eigenschaften aus der ACPI-Datenbeschreibung: Ownership, Lock, Host-Ownership, Interrupt-Status, Interrupt-Enable sowie Gruppennamen und Pad-Anzahl. Für diese Firmwarebeschreibung wird eine Community pro ACPI-Gerät aufgebaut. Das erklärt, warum die Samsung-Knoten `GPI0`, `GPI1` und weitere einzeln betrachtet werden müssen. Die gemeinsame VoodooGPIO-Basisklasse müsste einen variablen Interrupt-Status-Offset erhalten; bislang verwendet sie fest `0x100`. Zusätzlich muss die Gerätenub-Zuordnung den richtigen Community-Anbieter finden. Es wurde kein Linux-GPIO-Code übernommen oder dessen GPLv2-Lizenz geändert.

## Erster nativer Prüfpfad

Im [gepinnten I2C-Nub](https://github.com/VoodooI2C/VoodooI2C/blob/5c0c99cc451b03790c684ce6552c3f61620ad46b/VoodooI2C/VoodooI2C/VoodooI2CDevice/VoodooI2CDeviceNub.cpp) existieren `-vi2c-force-polling` und die PCI-Eigenschaft `force-polling`. Der APIC-Check erfolgt vorher und bleibt bei einem als gültig erkannten APIC aktiv. Der [HID-Satellit](https://github.com/VoodooI2C/VoodooI2CHID/blob/27277936bc16c215ae7f9b31e76e3231778d04bd/VoodooI2CHID/VoodooI2CHIDDevice.cpp) besitzt einen Timer-Pfad, falls keine Interrupt-Event-Quelle zustande kommt. Das eröffnet einen experimentellen ersten Test, garantiert aber kein funktionierendes Touchpad und kein sicheres Resume. Dieses Build setzt Polling nicht automatisch und ändert keine ACPI-Tabelle.

Eine spätere OpenCore-Einbindung muss die tatsächlichen `OSBundleLibraries` beachten: GPIO und Services vor dem I2C-Bus, HID nach dem Bus; VoodooInput muss für die veröffentlichte Multitouch-Engine verfügbar sein. Nur eine VoodooInput-Instanz darf aktiviert werden, falls andere Eingabetreiber sie ebenfalls mitbringen. Zielgeräte werden zunächst einzeln geprüft: Bus-Matching, ACPI-Nub, HID-Descriptor, Report-Descriptor, Eingabe, danach Wake/Resume und Last.

Vorhandene Logs nennen getrennt Controller, Gerätenub und HID-Satellit. Für Fehlersuche sind der ausgewertete Interrupt-Pfad, Meldungen zur Controller-/API-Öffnung, HID-Reset, Report-Längen und Resume entscheidend. Es wird keine neue Behauptung persistenter Kernel-Traces oder bereits gemessener Ziel-Hardwarewerte erzeugt.

## Reproduzierbarer Build und Prüfung

Der Workflow `.github/workflows/i2c-build.yml` verwendet einen echten Intel-macOS-Buildhost, gepinnte Quellen und ausschließlich die benötigten GPIO-/HID-Submodule. Die eigene Vorbereitung kontrolliert Git-Commit-Identität, unveränderte Quellen, Submodule, ursprüngliche Plist- und Xcode-Projekt-Hashes. Sie erstellt neue Quellsnapshots; unter Windows wird die Git-Archiv-CRLF-Konvertierung ausdrücklich deaktiviert. Bestehende Checkouts bleiben unverändert.

`prepare_build.py` entfernt aktive Upstream-Paketierungsphasen und unnötige Satelliten-Buildabhängigkeiten. `build_i2c.py` baut die fünf Xcode-Zielprojekte getrennt und prüft für jedes Bundle einen echten x86_64-Mach-O vom Typ `MH_KEXT_BUNDLE`, Modul- und Plist-Identität, Version, fehlende GPU-Abhängigkeit und fehlende verschachtelte Kexts. Diese Strukturprüfungen ersetzen keine Kernel-ABI- oder Geräteprüfung unter Tahoe.

Das Experimental-ZIP enthält die fünf unabhängigen Bundles, einen Buildbericht mit SHA-256-Werten und Commit-Pins sowie die zugehörigen Upstream- und Projektquellen mit Original-Lizenztexten. Interne Pläne und Aufgabenlisten, Git-Metadaten, Windows-Gerätetreiber und private EFI-Daten werden nicht paketiert. VoodooInput behält GPLv2; Eigenanteile verwenden GPLv3. Der externe MacKernelSDK-Stand ist als Build-Abhängigkeit gepinnt und wird nicht als eigener Treibercode ausgegeben.

Dieser Upstream-Build linkt `MacKernelSDK/Library/x86_64/libkmod.a` mit SHA-256 `7a751671bbe96c9c7b5416078df600b768c00c0dc5178d8acd8f52539c7ccd4e`. Deshalb liegen der originale Apple-Public-Source-License-2.0-Text, SDK-Herkunftshinweise, die im gepinnten SDK verfügbaren `Library/kmod`-Quellen und ein ausdrücklich zugeordneter `SOURCE-NOTICES-SDK.txt` im Paket. Der Buildbericht verzeichnet Pin, Bibliotheks-Hash und zugeordnete Lizenz-/Quellpfade. Die vorgebaute Bibliothek wurde nicht aus diesen Quellen neu gebaut und byteweise verglichen; es wird kein solcher Nachweis behauptet. Apple-/SDK-Bestandteile werden nicht als GPLv3 relizenziert.

Ein zusätzliches OpenCore-Fragment enthält die fünf Einträge in Abhängigkeitsreihenfolge, auf Darwin 25 begrenzt und vollständig deaktiviert. Es wird nicht automatisch in eine EFI geschrieben. Erst ein bestätigter Boot-/Secure-Boot-Pfad und die Prüfung bereits vorhandener Eingabe-Kexts erlauben eine konkrete Ziel-Einbindung ohne doppelte VoodooInput-Instanz.

Unter Windows wurde die Vorbereitung mit tatsächlichen gepinnten Git-Quellen ausgeführt. Alle acht bisherigen Source-/PCI-Matching-Prüfungen und 18 Vorbereitungs-/Paketprüfungen bestehen. Die Paketprüfung nutzt ausdrücklich synthetische, nicht ausführbare Datei-Fixtures; sie belegt den Archivaufbau und die deaktivierten OpenCore-Einträge. Der erste macOS-Link-/Paketlauf steht noch aus. Geladene Ziel-Kexts, Samsung-GPIO, Touchpad-/Touchscreen-Eingaben, Power/Resume und firmwareseitiger UEFI Secure Boot bleiben unbestätigt. Der Build ändert keine EFI, Secure-Boot-Policy oder Datenträger.
