# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
"""Host model and preparation checks, never macOS device or MMIO tests."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
PREPARED = None
COMPILER = None


class GPIOChecks(unittest.TestCase):
    def test_actual_cpp_bounds_ownership_and_resource_model(self):
        with tempfile.TemporaryDirectory(dir=PREPARED.parent) as temporary:
            binary = Path(temporary) / 'gpio-host-test'
            compiler = [COMPILER, 'c++'] if Path(COMPILER).name.lower() in ('zig', 'zig.exe') else [COMPILER]
            include = PREPARED / 'VoodooI2C/Dependencies/VoodooI2CACPIResourcesParser'
            code = (include / 'VoodooI2CACPIResourcesParser.cpp').read_text()
            # The reader uses only memcpy/memset and plain C++ types. Its unused
            # kernel include is removed only in this host adapter; no API is stubbed.
            self.assertEqual(code.count('#include <IOKit/IOLib.h>'), 1)
            adapted = Path(temporary) / 'ResourceReaderHost.cpp'
            adapted.write_text(code.replace('#include <IOKit/IOLib.h>', ''), encoding='utf-8')
            subprocess.run([*compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-O1',
                            '-I', str(include), str(HERE / 'test_gpio_model.cpp'), str(adapted), '-o', str(binary)], check=True, timeout=120)
            subprocess.run([str(binary)], check=True, timeout=30)

    def test_new_match_is_disabled_and_legacy_personalities_are_preserved(self):
        info = plistlib.loads((PREPARED / 'VoodooI2C/Dependencies/VoodooGPIO/VoodooGPIO/Info.plist').read_bytes())
        entry = info['IOKitPersonalities'].pop('VoodooGPIOSamsungLunarLake')
        self.assertEqual(entry['IONameMatch'], ['INTC105D', 'INTC105F'])
        self.assertEqual(entry['IOProviderClass'], 'IOACPIPlatformDevice')
        self.assertIs(entry['LunarBridgeGPIOEnabled'], False)
        self.assertEqual(entry['IOClass'], 'VoodooGPIOSamsungLunarLake')
        self.assertNotIn('INTC105D', str(info['IOKitPersonalities']))
        self.assertNotIn('INTC105F', str(info['IOKitPersonalities']))
        self.assertEqual(info['OSBundleLibraries']['com.apple.iokit.IOPCIFamily'], '2.9')

    def test_patch_changes_and_owned_sources_are_hash_recorded(self):
        report = json.loads((PREPARED / 'preparation.json').read_text())['gpio_port']
        manifest = json.loads((HERE / 'manifest.json').read_text())
        self.assertEqual(report['patch_sha256'], manifest['patch_sha256'])
        self.assertEqual(report['patch_sha256'], hashlib.sha256((HERE / 'VoodooI2C-Samsung-GPIO.patch').read_bytes()).hexdigest())
        self.assertEqual(len(report['changes']), 10)
        for relative, change in report['changes'].items():
            self.assertEqual(hashlib.sha256((PREPARED / 'VoodooI2C' / relative).read_bytes()).hexdigest(), change['after_sha256'])
        self.assertIs(report['default_enabled'], False)
        self.assertIs(report['native_hardware_tested'], False)

    def test_xcode_compiles_the_new_native_driver(self):
        data = (PREPARED / 'VoodooI2C/Dependencies/VoodooGPIO/VoodooGPIO.xcodeproj/project.pbxproj').read_text()
        phases = re.search(r'/\* Begin PBXSourcesBuildPhase section \*/(.*?)/\* End PBXSourcesBuildPhase section \*/', data, re.S).group(1)
        self.assertIn('VoodooGPIOSamsungLunarLake.cpp in Sources', phases)
        self.assertIn('VoodooGPIOIntel.cpp in Sources', phases)

    def test_opt_in_precedes_every_startup_hardware_operation(self):
        source = (HERE / 'VoodooGPIOSamsungLunarLake.cpp').read_text()
        start = source[source.index('bool VoodooGPIOSamsungLunarLake::start('):source.index('bool VoodooGPIOSamsungLunarLake::eligible(')]
        self.assertLess(start.index('LunarBridgeGPIOEnabled'), start.index('samsungControllerPresent()'))
        self.assertLess(start.index('LunarBridgeGPIOEnabled'), start.index('readDescription(device)'))
        self.assertLess(start.index('LunarBridgeGPIOEnabled'), start.index('mapDeviceMemoryWithIndex'))
        self.assertNotIn('writel(', start)
        self.assertIn('CPUJTAG', source)

    def test_samsung_nub_requires_the_selected_resource_source(self):
        source = (PREPARED / 'VoodooI2C/VoodooI2C/VoodooI2C/VoodooI2CDevice/VoodooI2CDeviceNub.cpp').read_text()
        method = source[source.index('VoodooGPIO* VoodooI2CDeviceNub::getGPIOController()'):source.index('IOReturn VoodooI2CDeviceNub::getInterruptType(')]
        self.assertIn('gpioResourceSource', method)
        self.assertIn('LunarBridgeGPIOResourceSource', method)
        self.assertIn('0xC1DD', method)
        self.assertIn('return nullptr;', method)
        self.assertIn('gpio_controller->retain()', method)

    def test_acpi_resource_walker_is_bounded_and_iterative(self):
        source = (PREPARED / 'VoodooI2C/Dependencies/VoodooI2CACPIResourcesParser/VoodooI2CACPIResourcesParser.cpp').read_text()
        method = source[source.index('void VoodooI2CACPIResourcesParser::parseACPIResources('):]
        self.assertEqual(method.count('parseACPIResources('), 1)
        self.assertIn('item > sz - offset', method)
        self.assertIn('sz - offset < 3', method)
        self.assertIn('resourceSource(res + offset, sz - offset', source)

    def test_reentrant_irq_guards_and_retains_reject_removed_protections(self):
        source = (HERE / 'VoodooGPIOSamsungLunarLake.cpp').read_text()

        def verify(code):
            dispatch = code[code.index('void VoodooGPIOSamsungLunarLake::dispatch()'):code.index('IOReturn VoodooGPIOSamsungLunarLake::getInterruptType(')]
            guard = 'if (!ready || !controllerIsAwake || !registers || !mapping) return;'
            self.assertEqual(dispatch.count(guard), 2)
            self.assertLess(dispatch.index(guard), dispatch.index('mapping->getLength()'))
            callback_end = dispatch.index('target->release();') + len('target->release();')
            self.assertIn(guard, dispatch[callback_end:])
            self.assertNotRegex(re.sub(r'//[^\n]*', '', dispatch[callback_end:]), r'\bentry\s*\.\s*\w')
            self.assertLess(dispatch.index('++delivered'), dispatch.index('action(target'))
            interrupt = code[code.index('void VoodooGPIOSamsungLunarLake::interrupt('):code.index('void VoodooGPIOSamsungLunarLake::dispatch()')]
            self.assertLess(interrupt.index('controller->retain();'), interrupt.index('controller->dispatch();'))
            self.assertLess(interrupt.index('controller->dispatch();'), interrupt.index('controller->release();'))
            event = code[code.index('bool checkForWork() override'):code.index('OSDefineMetaClassAndStructors(LunarBridgeGPIOInterruptSource,')]
            self.assertLess(event.index('retain();'), event.index('IOInterruptEventSource::checkForWork();'))
            self.assertLess(event.index('IOInterruptEventSource::checkForWork();'), event.index('release();'))
            gettype = code[code.index('if (request.operation == GetType)'):code.index('Registration &entry = registrations[request.pin];')]
            self.assertIn('getProvider()->getInterruptType(0,', gettype)
            loss = dispatch[dispatch.index('if (!eligible(pin))'):dispatch.index('// Acknowledge only our pin')]
            self.assertIn('interruptSource->disable();', loss)
            self.assertNotIn('writel(', loss)
            self.assertNotIn('mask(', loss)

        verify(source)
        guard = 'if (!ready || !controllerIsAwake || !registers || !mapping) return;'
        variants = {
            'missing_first_state_gate': source.replace(guard, '', 1),
            'missing_post_callback_gate': source[:source.rindex(guard)] + source[source.rindex(guard) + len(guard):],
            'missing_controller_hold': source.replace('controller->retain();', '(void)0;'),
            'missing_event_source_hold': source.replace('        retain();\n        const bool more', '        (void)0;\n        const bool more'),
            'foreign_pin_write_after_loss': source.replace('ready = false; // A changed owner/mux', 'writel(0, registers); ready = false; // A changed owner/mux'),
            'wrong_trigger_namespace': source.replace('getProvider()->getInterruptType(0,', 'getProvider()->setInterruptTypeForPin(0,'),
        }
        for label, code in variants.items():
            with self.subTest(removed=label), self.assertRaises((AssertionError, ValueError)):
                verify(code)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', required=True)
    parser.add_argument('--compiler', default='clang++')
    args = parser.parse_args()
    PREPARED = Path(args.prepared).resolve(strict=True)
    COMPILER = args.compiler
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(GPIOChecks))
    report = {'test_type': 'Host C++ model and source/preparation checks; no device/MMIO tests',
              'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
              'passed': result.wasSuccessful(), 'driver_loaded': False, 'native_hardware_tested': False}
    (PREPARED.parent / 'gpio-test-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
