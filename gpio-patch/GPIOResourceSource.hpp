// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
#ifndef LUNARBRIDGE_GPIO_RESOURCE_SOURCE_HPP
#define LUNARBRIDGE_GPIO_RESOURCE_SOURCE_HPP
#include <stdint.h>
#include <stddef.h>
namespace LunarBridgeGPIO {
inline uint16_t word(const uint8_t *data) { return static_cast<uint16_t>(data[0] | data[1] << 8); }
inline bool resourceSource(const uint8_t *data, uint32_t available, char *output, size_t capacity) {
    if (!data || !output || capacity < 2 || available < 25 || data[0] != 0x8C || data[3] != 1 || data[4] > 1 || data[16] != 0) return false;
    uint32_t size = static_cast<uint32_t>(word(data + 1)) + 3;
    uint32_t pins = word(data + 14), source = word(data + 17), vendor = word(data + 19), vendorBytes = word(data + 21);
    if (size > available || size < 25 || pins < 23 || pins + 2 != source || source >= size ||
        vendor < source || vendor > size || vendorBytes > size - vendor || word(data + pins) == 0xFFFF) return false;
    uint32_t end = source;
    while (end < vendor && data[end]) {
        const uint8_t ch = data[end];
        if (!((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '.' || ch == '\\')) return false;
        if (end - source + 1 >= capacity) return false;
        ++end;
    }
    if (end == source || end >= vendor || data[end] != 0) return false;
    for (uint32_t index = source; index < end; ++index) output[index - source] = static_cast<char>(data[index]);
    output[end - source] = 0;
    return true;
}
}
#endif
