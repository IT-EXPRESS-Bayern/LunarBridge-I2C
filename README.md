# LunarBridge I2C

Independent I2C controller and HID research for the Samsung Galaxy Book5 Pro NP960XHA-KG3DE. This repository builds separately from the LunarBridge GPU project.

The Samsung source patch narrowly matches Intel Lunar Lake I2C controllers `8086:A878`–`8086:A87B` with subsystem `144D:C1DD`. It does not implement a new GPIO platform or establish working touchpad/touchscreen support under macOS Tahoe.

The build produces five independent kernel bundles: `VoodooGPIO.kext`, `VoodooI2CServices.kext`, `VoodooI2C.kext`, `VoodooI2CHID.kext`, and `VoodooInput.kext`. Their dependency order and remaining target-device checks are documented in [the I2C backend](docs/I2C_BACKEND.md).

Build and source verification run in the separate [I2C workflow](.github/workflows/i2c-build.yml). Sources and actions are pinned. No firmware Secure Boot settings, Windows drivers, storage partitions or EFI files are changed by these scripts. Unsigned build artifacts require a separately qualified native boot and trust-chain setup before target testing.

Original upstream licenses and copyrights remain intact. `LICENSE` covers this repository's own GPLv3 build, validation and patch contributions. The separately built VoodooInput component retains its original GPLv2 license; see [NOTICE](NOTICE).

Der erfolgreiche Online-Prüflauf und sein exakter Quellstand sind in [docs/CI_VALIDATION.json](docs/CI_VALIDATION.json) festgehalten. Spätere Dokumentations-Commits sind davon getrennt; dieser CI-Lauf ist kein Gerätefunktionstest auf dem Samsung.
