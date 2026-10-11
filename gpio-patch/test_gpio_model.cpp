// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
// Executable host tests of the actual bounded model; never accesses real MMIO.
#include "LunarLakeGPIOModel.hpp"
#include "GPIOResourceSource.hpp"
#include "VoodooI2CACPIResourcesParser.hpp"
#ifdef NDEBUG
#undef NDEBUG // Every host-model assertion must execute with every compiler/optimization mode.
#endif
#include <assert.h>
#include <stdio.h>
#include <string.h>
using namespace LunarBridgeGPIO;
int main() {
    Layout layout {0x10000, 3, 16, 0xD0, 0x110, 0x130, 0x200, 0x210, {{26,0x600},{25,0x7A0},{14,0x930}}};
    assert(valid(layout, 0x10000));
    assert(!valid(layout, 0xFFFF) && !valid(layout, 0x10001));
    Layout invalid = layout;
    invalid.groups[1].pads = 0xFFFFFFFFU; assert(!valid(invalid, 0x10000));
    invalid = layout; invalid.groups[2].configuration = 0xFFFFFFF0U; assert(!valid(invalid, 0x10000));
    invalid = layout; invalid.interruptStatus = 0x100; assert(!valid(invalid, 0x10000));
    invalid = layout; invalid.stride = 8; assert(!valid(invalid, 0x10000));
    assert(!range(3, 4, 0x10000) && !range(0x10000, 4, 0x10000) && !range(0xFFFFFFFCU, 8, 0x10000));
    for (unsigned device = 0; device < 65536; ++device) {
        bool expected = device >= 0xA878 && device <= 0xA87B;
        assert(isSamsung(0x8086, static_cast<uint16_t>(device), 0x144D, 0xC1DD) == expected);
        assert(!isSamsung(0x1234, static_cast<uint16_t>(device), 0x144D, 0xC1DD));
        assert(!isSamsung(0x8086, static_cast<uint16_t>(device), 0x144D, 0xFFFF));
    }
    Pin pad {};
    assert(!pin(layout, -1, 0x10000, pad) && !pin(layout, 65, 0x10000, pad));
    for (int number = 0; number < 65; ++number) {
        assert(pin(layout, number, 0x10000, pad));
        assert(pad.bit < 32 && pad.ownershipShift <= 28 && range(pad.configuration, 4, 0x10000));
    }
    assert(pin(layout, 44, 0x10000, pad));
    assert(pad.group == 1 && pad.bit == 18 && pad.configuration == 0x8C0 && pad.padOwnership == 0xE8 && pad.ownershipShift == 8);
    assert(pad.configurationLock == 0x118 && pad.hostOwnership == 0x134 && pad.interruptStatus == 0x204 && pad.interruptEnable == 0x214);
    const uint32_t host = 1U << 18, input = 1U << 8;
    assert(eligible(pad, 0, host, 0, input));
    assert(!eligible(pad, 1U << 8, host, 0, input) && !eligible(pad, 0, 0, 0, input));
    assert(!eligible(pad, 0, host, host, input) && !eligible(pad, 0, host, 0, input | (1U << 10)));
    assert(!eligible(pad, 0, host, 0, input | (1U << 20)) && !eligible(pad, 0, host, 0, input | (1U << 9)));
    assert(!eligible(pad, 0, host, 0, 0)); // output-enabled pins are never taken
    assert(pin(layout, 51, 0x10000, pad) && !eligible(pad, 0, 1, 0, input)); // CPUJTAG excluded
    const uint32_t fields = (3U << 25) | (1U << 23), original = 0xC07A0193U;
    uint32_t configured = 0;
    for (unsigned type = 0; type < 256; ++type) {
        bool expected = type == 1 || type == 2 || type == 3 || type == 4 || type == 8;
        assert(irqConfiguration(original, type, configured) == expected);
        if (expected) assert((configured & ~fields) == (original & ~fields));
    }
    assert(irqConfiguration(input, 8, configured) && (configured & fields) == (1U << 23));

    // The AML template contains one pin at offset23, source25, vendor/end35.
    uint8_t resource[35] {};
    resource[0] = 0x8C; resource[1] = 32; resource[3] = 1; resource[14] = 23; resource[17] = 25; resource[19] = 35;
    resource[23] = 44; memcpy(resource + 25, "\\_SB.GPI1", 10);
    char source[64] {};
    assert(resourceSource(resource, sizeof(resource), source, sizeof(source)) && !strcmp(source, "\\_SB.GPI1"));
    for (unsigned truncated = 0; truncated < sizeof(resource); ++truncated)
        assert(!resourceSource(resource, truncated, source, sizeof(source)));
    uint8_t bad[35];
    memcpy(bad, resource, sizeof(bad)); bad[17] = 0xFF; bad[18] = 0xFF; assert(!resourceSource(bad, sizeof(bad), source, sizeof(source)));
    memcpy(bad, resource, sizeof(bad)); bad[14] = 0xFF; bad[15] = 0xFF; assert(!resourceSource(bad, sizeof(bad), source, sizeof(source)));
    memcpy(bad, resource, sizeof(bad)); bad[34] = 'A'; assert(!resourceSource(bad, sizeof(bad), source, sizeof(source)));
    memcpy(bad, resource, sizeof(bad)); bad[16] = 1; assert(!resourceSource(bad, sizeof(bad), source, sizeof(source)));
    memcpy(bad, resource, sizeof(bad)); bad[19] = 36; assert(!resourceSource(bad, sizeof(bad), source, sizeof(source)));
    memcpy(bad, resource, sizeof(bad)); bad[23] = bad[24] = 0xFF; assert(!resourceSource(bad, sizeof(bad), source, sizeof(source)));
    assert(!resourceSource(resource, sizeof(resource), source, 9));
    for (unsigned truncated = 0; truncated < sizeof(resource); ++truncated) {
        VoodooI2CACPIResourcesParser reader;
        reader.parseACPIResources(resource, 0, truncated);
        assert(!reader.found_gpio_interrupts);
    }
    const uint8_t flags[] = {0,1,2,3,4,5,6,7};
    const int types[] = {4,1,8,2,0,3,0,0};
    for (unsigned index = 0; index < sizeof(flags); ++index) {
        resource[7] = flags[index];
        VoodooI2CACPIResourcesParser reader;
        reader.parseACPIResources(resource, 0, sizeof(resource));
        assert(reader.found_gpio_interrupts == (types[index] != 0));
        if (types[index]) {
            assert(reader.gpio_interrupts.pin_number == 44 && reader.gpio_interrupts.irq_type == types[index]);
            assert(!strcmp(reader.gpio_interrupts.resource_source, "\\_SB.GPI1"));
        }
    }
    uint8_t many[65536] {};
    for (unsigned index = 0; index < sizeof(many); index += 4) {
        many[index] = 0x80; many[index + 1] = 1;
    }
    VoodooI2CACPIResourcesParser reader;
    reader.parseACPIResources(many, 0, sizeof(many)); // many descriptors, bounded walker without recursive stack growth
    assert(!reader.found_gpio_interrupts && !reader.found_i2c);
    puts("GPIO model and actual resource reader: layout, all device IDs, pin bounds, ownership, IRQ polarity and truncations passed");
    return 0;
}
