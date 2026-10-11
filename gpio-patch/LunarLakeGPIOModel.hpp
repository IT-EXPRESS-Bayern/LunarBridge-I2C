// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
// Independently authored bounded layout model; no Linux implementation copied.
#ifndef LUNARBRIDGE_GPIO_MODEL_HPP
#define LUNARBRIDGE_GPIO_MODEL_HPP
#include <stdint.h>

namespace LunarBridgeGPIO {
struct Group { uint32_t pads, configuration; };
struct Layout {
    uint32_t revision, groupCount, stride, padOwnership, configurationLock;
    uint32_t hostOwnership, interruptStatus, interruptEnable;
    Group groups[3];
};
struct Pin {
    uint32_t group, bit, configuration, padOwnership, ownershipShift;
    uint32_t configurationLock, hostOwnership, interruptStatus, interruptEnable;
};
inline bool range(uint32_t offset, uint32_t bytes, uint64_t window) {
    return !(offset & 3U) && offset <= window && bytes <= window - offset;
}
inline bool isSamsung(uint16_t vendor, uint16_t device, uint16_t subsystemVendor, uint16_t subsystem) {
    return vendor == 0x8086 && device >= 0xA878 && device <= 0xA87B &&
           subsystemVendor == 0x144D && subsystem == 0xC1DD;
}
inline bool valid(const Layout &layout, uint64_t window) {
    // This first port accepts only the Samsung GPI1 profile observed in local ACPI source.
    if (window != 0x10000 || layout.revision != 0x10000 || layout.groupCount != 3 || layout.stride != 16 ||
        layout.padOwnership != 0xD0 || layout.configurationLock != 0x110 || layout.hostOwnership != 0x130 ||
        layout.interruptStatus != 0x200 || layout.interruptEnable != 0x210) return false;
    const uint32_t counts[3] = {26, 25, 14}, offsets[3] = {0x600, 0x7A0, 0x930};
    for (unsigned index = 0; index < 3; ++index) {
        if (layout.groups[index].pads != counts[index] || layout.groups[index].configuration != offsets[index] ||
            !range(offsets[index], counts[index] * layout.stride, window)) return false;
    }
    return range(layout.padOwnership, 40, window) && range(layout.configurationLock, 24, window) &&
           range(layout.hostOwnership, 12, window) && range(layout.interruptStatus, 12, window) && range(layout.interruptEnable, 12, window);
}
inline bool pin(const Layout &layout, int number, uint64_t window, Pin &result) {
    if (!valid(layout, window) || number < 0 || number >= 65) return false;
    uint32_t base = 0, ownerRegister = 0;
    for (uint32_t group = 0; group < 3; ++group) {
        if (static_cast<uint32_t>(number) < base + layout.groups[group].pads) {
            const uint32_t local = static_cast<uint32_t>(number) - base;
            result = {group, local, layout.groups[group].configuration + local * layout.stride,
                      layout.padOwnership + ownerRegister * 4 + local / 8 * 4, (local % 8) * 4,
                      layout.configurationLock + group * 8, layout.hostOwnership + group * 4,
                      layout.interruptStatus + group * 4, layout.interruptEnable + group * 4};
            return range(result.configuration, 4, window) && range(result.padOwnership, 4, window) &&
                   range(result.configurationLock, 8, window) && range(result.hostOwnership, 4, window) &&
                   range(result.interruptStatus, 4, window) && range(result.interruptEnable, 4, window);
        }
        base += layout.groups[group].pads;
        ownerRegister += (layout.groups[group].pads + 7) / 8;
    }
    return false;
}
inline bool irqConfiguration(uint32_t original, unsigned type, uint32_t &configured) {
    // Keep routing, mux, pulls, output and every unrelated pad field intact.
    const uint32_t eventMask = 3U << 25, invert = 1U << 23;
    uint32_t value = original & ~(eventMask | invert);
    switch (type) {
        case 1: value |= 1U << 25; break; // rising edge
        case 2: value |= (1U << 25) | invert; break; // falling edge
        case 3: value |= 3U << 25; break; // both edges
        case 4: break; // high level
        case 8: value |= invert; break; // low level
        default: return false;
    }
    configured = value;
    return true;
}
inline bool eligible(const Pin &pin, uint32_t padOwner, uint32_t hostOwner, uint32_t lock, uint32_t configuration) {
    if (pin.group > 1 || pin.bit >= 32 || pin.ownershipShift > 28) return false;
    const uint32_t bit = uint32_t(1) << pin.bit;
    return !(padOwner & (0xFU << pin.ownershipShift)) && (hostOwner & bit) && !(lock & bit) &&
           !(configuration & ((0xFU << 10) | (0xFU << 17) | (1U << 9))) && (configuration & (1U << 8));
}
}
#endif
