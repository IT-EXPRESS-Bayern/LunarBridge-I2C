# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
"""Apply the bounded, disabled-by-default GPI1 source port to a verified snapshot."""
import difflib
import hashlib
import json
from pathlib import Path
import plistlib
import re

HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(text, original, replacement):
    if text.count(original) != 1:
        raise ValueError('Pinned GPIO/core source structure changed')
    return text.replace(original, replacement, 1)


def project_sources(text):
    names = ('VoodooGPIOSamsungLunarLake.cpp', 'VoodooGPIOSamsungLunarLake.hpp', 'LunarLakeGPIOModel.hpp')
    for name in names:
        reference = hashlib.sha256(('LunarBridge/file/' + name).encode()).hexdigest()[:24].upper()
        build = hashlib.sha256(('LunarBridge/build/' + name).encode()).hexdigest()[:24].upper()
        if reference in text or build in text:
            raise ValueError('New GPIO project object collides with an existing object')
        kind = 'Sources' if name.endswith('.cpp') else 'Headers'
        filetype = 'sourcecode.cpp.cpp' if name.endswith('.cpp') else 'sourcecode.cpp.h'
        text = once(text, '/* Begin PBXBuildFile section */', '/* Begin PBXBuildFile section */\n' +
            f'\t\t{build} /* {name} in {kind} */ = {{isa = PBXBuildFile; fileRef = {reference} /* {name} */; }};')
        text = once(text, '/* Begin PBXFileReference section */', '/* Begin PBXFileReference section */\n' +
            f'\t\t{reference} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = {filetype}; path = {name}; sourceTree = "<group>"; }};')
        text = once(text, '\t\t\t\t40AAD2D329B657EC001C60CD /* VoodooGPIO.cpp */,',
            f'\t\t\t\t{reference} /* {name} */,\n\t\t\t\t40AAD2D329B657EC001C60CD /* VoodooGPIO.cpp */,')
        marker = '/* Begin PBX' + kind + 'BuildPhase section */'
        start = text.index(marker)
        location = text.index('\t\t\tfiles = (', start) + len('\t\t\tfiles = (')
        text = text[:location] + f'\n\t\t\t\t{build} /* {name} in {kind} */,' + text[location:]
    return text


def derive(root, manifest):
    originals, changes = {}, {}
    for relative, expected in manifest['snapshot_input_sha256'].items():
        data = (root / relative).read_bytes()
        if sha(data) != expected:
            raise ValueError('Pinned pre-GPIO source hash mismatch: ' + relative)
        originals[relative] = data
    parser_h = 'Dependencies/VoodooI2CACPIResourcesParser/VoodooI2CACPIResourcesParser.hpp'
    parser_c = 'Dependencies/VoodooI2CACPIResourcesParser/VoodooI2CACPIResourcesParser.cpp'
    nub_h = 'VoodooI2C/VoodooI2C/VoodooI2CDevice/VoodooI2CDeviceNub.hpp'
    nub_c = 'VoodooI2C/VoodooI2C/VoodooI2CDevice/VoodooI2CDeviceNub.cpp'
    gpio_i = 'Dependencies/VoodooGPIO/VoodooGPIO/Info.plist'
    gpio_p = 'Dependencies/VoodooGPIO/VoodooGPIO.xcodeproj/project.pbxproj'
    text = originals[parser_h].decode('utf-8')
    text = once(text, '    int irq_type;', '    int irq_type;\n    char resource_source[64];')
    changes[parser_h] = text.encode('utf-8')
    text = originals[parser_c].decode('utf-8')
    text = once(text, '#include "linuxirq.hpp"', '#include "linuxirq.hpp"\n#include "GPIOResourceSource.hpp"')
    text = once(text, '    found_gpio_interrupts = false;\n    found_i2c = false;',
        '    found_gpio_interrupts = found_gpio_io = found_i2c = false;\n'
        '    memset(&i2c_info, 0, sizeof(i2c_info));\n    memset(&gpio_interrupts, 0, sizeof(gpio_interrupts));\n'
        '    memset(&gpio_io, 0, sizeof(gpio_io));')
    text = once(text, '    uint8_t gpio_type = res[offset + 4];',
        '    char resource_source[64] = {};\n'
        '    if (!LunarBridgeGPIO::resourceSource(res + offset, sz - offset, resource_source, sizeof(resource_source))) return;\n'
        '    uint8_t gpio_type = res[offset + 4];')
    text = once(text, '        gpio_interrupts.pin_number = pin_number;',
        '        gpio_interrupts.pin_number = pin_number;\n'
        '        memcpy(gpio_interrupts.resource_source, resource_source, sizeof(resource_source));')
    text = once(text, '    if (gpio_type == 0) {',
        '    const unsigned polarity = (gpio_flags >> 1) & 3U;\n'
        '    if (gpio_type == 0 && (polarity == 3U || (!(gpio_flags & 1U) && polarity == 2U))) return;\n'
        '    if (gpio_type == 0) {')
    text = once(text, '                case 0:\n                    irq = IRQ_TYPE_EDGE_FALLING;\n                    break;\n                case 1:\n                    irq = IRQ_TYPE_EDGE_RISING;',
                     '                case 0:\n                    irq = IRQ_TYPE_EDGE_RISING;\n                    break;\n                case 1:\n                    irq = IRQ_TYPE_EDGE_FALLING;')
    begin = text.index('void VoodooI2CACPIResourcesParser::parseACPIResources(')
    text = text[:begin] + '''void VoodooI2CACPIResourcesParser::parseACPIResources(uint8_t const* res, uint32_t offset, uint32_t sz) {
    if (!res || offset > sz) return;
    while (offset < sz) {
        const uint8_t opcode = res[offset];
        uint32_t item = 0;
        if (opcode & 0x80) {
            if (sz - offset < 3) return;
            item = static_cast<uint32_t>(LunarBridgeGPIO::word(res + offset + 1)) + 3;
        } else item = (opcode & 7U) + 1;
        if (item > sz - offset) return;
        if (opcode == 0x8C && item >= 25) parseACPIGPIO(res, offset, offset + item);
        if (opcode == 0x8E && item >= 18) parseACPISerialBus(res, offset, offset + item);
        if ((opcode & 0xF8) == 0x78) return; // bounded ACPI EndTag, never recursive
        offset += item;
    }
}
'''
    changes[parser_c] = text.encode('utf-8')
    text = once(originals[nub_h].decode('utf-8'), '    VoodooGPIO* gpio_controller;', '    VoodooGPIO* gpio_controller = nullptr;')
    changes[nub_h] = text.encode('utf-8')
    text = originals[nub_c].decode('utf-8')
    text = once(text, '        gpio_irq = resource_parser.gpio_interrupts.irq_type;',
        '        gpio_irq = resource_parser.gpio_interrupts.irq_type;\n'
        '        setProperty("gpioResourceSource", resource_parser.gpio_interrupts.resource_source);')
    text = once(text, '    IOService* matched = waitForMatchingService(name_match, 1000000000);', '''    IOPCIDevice *pci = controller->nub->controller->physical_device.pci_device;
    if (pci && pci->configRead16(0) == 0x8086 && pci->configRead16(2) >= 0xA878 && pci->configRead16(2) <= 0xA87B &&
        pci->configRead16(0x2C) == 0x144D && pci->configRead16(0x2E) == 0xC1DD) {
        OSString *source = OSDynamicCast(OSString, getProperty("gpioResourceSource"));
        OSDictionary *properties = OSDictionary::withCapacity(1);
        if (!source || !source->getLength() || !properties) {
            OSSafeReleaseNULL(properties); name_match->release(); return nullptr;
        }
        properties->setObject("LunarBridgeGPIOResourceSource", source);
        name_match->setObject("IOPropertyMatch", properties);
        properties->release();
    }
    IOService* matched = waitForMatchingService(name_match, 1000000000);''')
    text = once(text, '    OSSafeReleaseNULL(matched);', '    if (gpio_controller) gpio_controller->retain();\n    OSSafeReleaseNULL(matched);')
    text = once(text, '    OSSafeReleaseNULL(work_loop);', '    OSSafeReleaseNULL(work_loop);\n    OSSafeReleaseNULL(gpio_controller);')
    changes[nub_c] = text.encode('utf-8')
    info = plistlib.loads(originals[gpio_i])
    info['IOKitPersonalities']['VoodooGPIOSamsungLunarLake'] = {
        'CFBundleIdentifier': 'org.coolstar.VoodooGPIO', 'IOClass': 'VoodooGPIOSamsungLunarLake',
        'IOProviderClass': 'IOACPIPlatformDevice', 'IONameMatch': ['INTC105D', 'INTC105F'],
        'LunarBridgeGPIOEnabled': False}
    info['OSBundleLibraries']['com.apple.iokit.IOPCIFamily'] = '2.9'
    changes[gpio_i] = plistlib.dumps(info, sort_keys=False)
    changes[gpio_p] = project_sources(originals[gpio_p].decode('utf-8')).encode('utf-8')
    for name, expected in manifest['owned_source_sha256'].items():
        data = (HERE / name).read_bytes()
        if sha(data) != expected: raise ValueError('GPIO port source hash mismatch: ' + name)
        folder = 'Dependencies/VoodooI2CACPIResourcesParser' if name == 'GPIOResourceSource.hpp' else 'Dependencies/VoodooGPIO/VoodooGPIO'
        relative = folder + '/' + name
        if (root / relative).exists(): raise ValueError('GPIO new source name collides with upstream')
        originals[relative] = b''
        changes[relative] = data
    patch = ''.join(''.join(difflib.unified_diff(originals[name].decode('utf-8').splitlines(keepends=True),
            changes[name].decode('utf-8').splitlines(keepends=True), fromfile='a/' + name if originals[name] else '/dev/null', tofile='b/' + name))
            for name in sorted(changes)).encode('utf-8')
    return originals, changes, patch


def apply(root):
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    originals, changes, patch = derive(root, manifest)
    expected_patch = HERE / 'VoodooI2C-Samsung-GPIO.patch'
    if patch != expected_patch.read_bytes() or sha(patch) != manifest['patch_sha256']:
        raise ValueError('GPIO reproducible source patch differs from the recorded patch')
    result = {}
    for name, data in changes.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        result[name] = {'before_sha256': sha(originals[name]), 'after_sha256': sha(data)}
    return {'patch_sha256': sha(patch), 'changes': result, 'community': 'GPI1',
            'default_enabled': False, 'implementation_experimental': True, 'native_hardware_tested': False}
