# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
"""Preparation/security checks; synthetic file fixtures never run as drivers."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import plistlib
import re
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_i2c
import prepare_build

PREPARED = None


def kernel_fixture(identifier='org.coolstar.VoodooGPIO', kind=11, version='1.1'):
    """Non-executable, synthetic Mach-O parser input; not a compiled kernel driver."""
    text_at, init_at, data_at, symbol_at, strings_at = 384, 400, 408, 608, 656
    strings = b'\0_kmod_info\0__start\0__stop\0'
    data = bytearray(strings_at + len(strings))
    struct.pack_into('<8I', data, 0, 0xfeedfacf, 0x01000007, 3, kind, 2, 336, 0, 0)
    struct.pack_into('<II16sQQQQiiII', data, 32, 0x19, 312, b'__TEXT', 0x1000, 224, text_at, 224, 7, 3, 3, 0)
    for index, (name, address, size, offset, flags) in enumerate((
        (b'__text', 0x1000, 16, text_at, 0x80000400),
        (b'__mod_init_func', 0x1010, 8, init_at, 9), (b'__data', 0x1018, 200, data_at, 0))):
        struct.pack_into('<16s16sQQ8I', data, 104 + index * 80, name, b'__TEXT', address, size, offset, 3, 0, 0, flags, 0, 0, 0)
    struct.pack_into('<6I', data, 344, 2, 24, symbol_at, 3, strings_at, len(strings))
    for offset, value in ((16, identifier), (80, version)):
        encoded = value.encode('ascii')
        data[data_at + offset:data_at + offset + len(encoded)] = encoded
    for index, (name, section, address) in enumerate((('_kmod_info', 3, 0x1018), ('__start', 1, 0x1000), ('__stop', 1, 0x1008))):
        struct.pack_into('<IBBHQ', data, symbol_at + index * 16, strings.index(name.encode() + b'\0'), 0x0f, section, 0, address)
    data[strings_at:] = strings
    return bytes(data)


def synthetic_bundle(parent, *, name='VoodooGPIO.kext', kind=11, binary_identifier=None, binary_version='1.1', libraries=None):
    folder = parent / name
    (folder / 'Contents/MacOS').mkdir(parents=True)
    identifier = prepare_build.MANIFEST['expected_bundles'][name]
    executable = Path(name).stem
    info = {'CFBundleIdentifier': identifier, 'CFBundleVersion': '1.1',
            'CFBundleExecutable': executable, 'CFBundlePackageType': 'KEXT',
            'OSBundleLibraries': libraries or {'com.apple.kpi.iokit': '13.0'}}
    if name == 'VoodooI2C.kext':
        info['IOKitPersonalities'] = {'VoodooI2CPCILunarLakeSamsungController':
            {'IOPCIPrimaryMatch': '0xa8788086 0xa8798086 0xa87a8086 0xa87b8086', 'IOPCISecondaryMatch': '0xc1dd144d'}}
    (folder / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    (folder / 'Contents/MacOS' / executable).write_bytes(kernel_fixture(binary_identifier or identifier, kind, binary_version))
    return folder


def archive_fixture(name, *, symbolic=False, content=b'source'):
    data = io.BytesIO()
    encoded_name = name.replace('\\', '?')  # ZipInfo otherwise normalizes Windows separators before validation.
    with zipfile.ZipFile(data, 'w') as archive:
        item = zipfile.ZipInfo(encoded_name)
        item.create_system = 3
        item.external_attr = ((stat.S_IFLNK | 0o777) if symbolic else (stat.S_IFREG | 0o644)) << 16
        archive.writestr(item, content)
    return data.getvalue().replace(encoded_name.encode('utf-8'), name.encode('utf-8'))


class PreparationChecks(unittest.TestCase):
    def test_every_source_pin_and_original_project_hash_is_recorded(self):
        report = json.loads((PREPARED / 'preparation.json').read_text())
        self.assertEqual(report['source_pins'], prepare_build.MANIFEST['sources'])
        self.assertEqual(len(report['project_changes']), 5)
        for relative, expected in prepare_build.MANIFEST['project_sha256'].items():
            self.assertEqual(report['project_changes'][relative]['original_sha256'], expected)
        for relative, alignment in prepare_build.MANIFEST['project_version_alignment'].items():
            self.assertEqual(report['project_changes'][relative]['version_alignment'], alignment)

    def test_every_prepared_module_version_matches_bundle_version(self):
        expected_versions = {'VoodooGPIO': '1.1', 'VoodooI2CServices': '1', 'VoodooI2C': '2.9.1',
                             'VoodooI2CHID': '1', 'VoodooInput': '1.1.7'}
        for target, relative in build_i2c.TARGETS:
            project = PREPARED / relative
            text = (project / 'project.pbxproj').read_text()
            values = {}
            for setting in ('MODULE_VERSION', 'CURRENT_PROJECT_VERSION', 'INFOPLIST_FILE'):
                found = re.findall(r'(?m)^\s*' + setting + r' = ([^;\n]+);$', text)
                self.assertEqual(len(found), 2, (target, setting))
                self.assertEqual(found[0], found[1], (target, setting))
                values[setting] = found[0].strip('"')
            current = values['CURRENT_PROJECT_VERSION'].replace('$(MODULE_VERSION)', values['MODULE_VERSION'])
            info = plistlib.loads((project.parent / values['INFOPLIST_FILE']).read_bytes())
            bundle = info['CFBundleVersion'].replace('$(CURRENT_PROJECT_VERSION)', current).replace('$(MODULE_VERSION)', values['MODULE_VERSION'])
            self.assertEqual(values['MODULE_VERSION'], expected_versions[target], target)
            self.assertEqual(current, bundle, target)
            self.assertEqual(values['MODULE_VERSION'], bundle, target)

    def test_only_active_source_phases_build_the_i2c_core(self):
        data = (PREPARED / 'VoodooI2C/VoodooI2C/VoodooI2C.xcodeproj/project.pbxproj').read_text()
        phases = re.search(r'buildPhases = \((.*?)\);', data, re.S).group(1)
        self.assertNotIn('Add GPIO and Services Plugin', phases)
        self.assertNotIn('Archive', phases)
        self.assertRegex(data, r'dependencies = \(\);')
        self.assertRegex(data, r'projectReferences = \(\);')
        for required in ('Sources', 'Frameworks', 'Headers', 'Resources'):
            self.assertIn(required, phases)

    def test_samsung_patch_is_exact_and_uses_no_gpio_alias(self):
        data = (PREPARED / 'VoodooI2C/VoodooI2C/VoodooI2C/Info.plist').read_bytes()
        expected = json.loads((prepare_build.HERE / 'patch-info.json').read_text())
        self.assertEqual(hashlib.sha256(data).hexdigest(), expected['patched_sha256'])
        personality = plistlib.loads(data)['IOKitPersonalities']['VoodooI2CPCILunarLakeSamsungController']
        self.assertEqual(personality['IOPCIPrimaryMatch'], '0xa8788086 0xa8798086 0xa87a8086 0xa87b8086')
        self.assertEqual(personality['IOPCISecondaryMatch'], '0xc1dd144d')
        gpio = plistlib.loads((PREPARED / 'VoodooI2C/Dependencies/VoodooGPIO/VoodooGPIO/Info.plist').read_bytes())
        self.assertNotIn('INTC105D', str(gpio['IOKitPersonalities']))
        self.assertNotIn('INTC105F', str(gpio['IOKitPersonalities']))

    def test_prepared_project_library_paths_are_portable(self):
        for relative in prepare_build.MANIFEST['project_sha256']:
            root = PREPARED / ('VoodooInput' if relative.startswith('VoodooInput.xcodeproj') else 'VoodooI2C')
            text = (root / relative).read_text()
            found = re.search(r'path = "([^"\n]*libkmod\.a)"; sourceTree = "<group>";', text)
            self.assertIsNotNone(found, relative)
            self.assertFalse(Path(found.group(1)).is_absolute())
            self.assertNotIn(':', found.group(1))
            self.assertTrue((root.joinpath(relative).parent.parent / found.group(1)).resolve().is_file())

    def test_original_license_texts_are_present_and_separate(self):
        self.assertIn('Version 3, 29 June 2007', (PREPARED / 'VoodooI2C/LICENSE.txt').read_text())
        self.assertIn('Version 3, 29 June 2007', (PREPARED / 'VoodooI2C/Dependencies/VoodooGPIO/LICENSE.md').read_text())
        self.assertIn('Version 2, June 1991', (PREPARED / 'VoodooInput/LICENSE.txt').read_text())
        self.assertFalse(prepare_build.MANIFEST['target']['gpio_support_implemented'])

    def test_hid_can_match_both_device_classes_via_pnp0c50(self):
        path = PREPARED / 'VoodooI2C/VoodooI2C Satellites/VoodooI2CHID/VoodooI2CHID/Info.plist'
        personalities = plistlib.loads(path.read_bytes())['IOKitPersonalities']
        self.assertEqual(personalities['VoodooI2CHIDDevice']['IOPropertyMatch']['compatible'], 'PNP0C50')
        self.assertIn('VoodooI2CHIDDevice Touchscreen HID Event Driver', personalities)
        self.assertIn('VoodooI2CHIDDevice Precision Touchpad HID Event Driver', personalities)

    def test_package_source_excludes_git_tasks_and_plan(self):
        paths = [name for _, name in build_i2c.source_files(PREPARED)]
        self.assertGreater(len(paths), 50)
        for name in paths:
            self.assertNotIn('.git', Path(name).parts)
            self.assertNotIn(Path(name).name.lower(), ('tasks.md', 'plan.md'))
        self.assertTrue(any(name.endswith('VoodooInput/LICENSE.txt') for name in paths))

    def test_changed_project_structure_fails_closed(self):
        with self.assertRaises(ValueError):
            prepare_build.patch_project('not the pinned project', sdk_library=Path('library/libkmod.a'), core=True)

    def test_rejects_archive_traversal_and_windows_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ('../escape.cpp', '/absolute.cpp', 'C:/private.cpp', 'folder\\escape.cpp'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    prepare_build.extract_archive(archive_fixture(name), Path(temporary))

    def test_rejects_archive_symbolic_links(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            prepare_build.extract_archive(archive_fixture('link.cpp', symbolic=True), Path(temporary))

    def test_archive_omits_task_files_and_preserves_source_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_build.extract_archive(archive_fixture('dir/TASKS.md'), root)
            self.assertFalse((root / 'dir/TASKS.md').exists())
            prepare_build.extract_archive(archive_fixture('file.cpp', content=b'line\n'), root)
            self.assertEqual((root / 'file.cpp').read_bytes(), b'line\n')


class PackageRejectionChecks(unittest.TestCase):
    def test_package_has_independent_kexts_source_and_disabled_fragment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            products, destination = root / 'products', root / 'package'
            destination.mkdir()
            for name in prepare_build.MANIFEST['expected_bundles']:
                synthetic_bundle(products, name=name)
            report = build_i2c.package(PREPARED, products, destination, {'test_type': 'Synthetic packaging fixture only'})
            self.assertFalse(report['native_hardware_tested'])
            with zipfile.ZipFile(destination / 'LunarBridge-I2C-Samsung-Experimental.zip') as archive:
                fragment = plistlib.loads(archive.read('OpenCore-Disabled-I2C-Fragment.plist'))
                entries = fragment['Kernel']['Add']
                self.assertEqual(len(entries), 5)
                self.assertTrue(all(entry['Enabled'] is False and entry['MinKernel'] == '25.0.0' for entry in entries))
                self.assertEqual(entries[-1]['BundlePath'], 'VoodooI2CHID.kext')
                paths = archive.namelist()
                self.assertTrue(all(Path(name).name.lower() not in ('tasks.md', 'plan.md') for name in paths))
                self.assertTrue(all('/PlugIns/' not in name for name in paths))
                self.assertIn('source/VoodooInput/LICENSE.txt', paths)
                self.assertIn('source/MacKernelSDK/LICENSE.txt', paths)
                self.assertIn('source/MacKernelSDK/Library/kmod/c_start.c', paths)
                self.assertIn('SOURCE-NOTICES-SDK.txt', paths)
                self.assertEqual(report['linked_sdk_library']['sha256'], prepare_build.MANIFEST['sources']['MacKernelSDK']['linked_library_sha256'])
                self.assertFalse(report['linked_sdk_library']['prebuilt_library_rebuilt_from_source_and_compared'])
                hashes = json.loads(archive.read('SOURCE-HASHES.json'))
                self.assertTrue(all(hashlib.sha256(archive.read(name)).hexdigest() == value for name, value in hashes.items()))

    def test_rejects_renamed_object_instead_of_real_kernel_bundle(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            build_i2c.validate_bundle(synthetic_bundle(Path(temporary), kind=1))

    def test_rejects_kernel_binary_from_another_driver(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            build_i2c.validate_bundle(synthetic_bundle(Path(temporary), binary_identifier='de.nordapps.LunarBridge'))

    def test_rejects_upstream_version_mismatch_with_controlled_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = synthetic_bundle(Path(temporary), binary_version='1.0.0d1')
            with self.assertRaises(ValueError) as caught:
                build_i2c.validate_bundle(bundle)
            message = str(caught.exception)
            self.assertIn('VoodooGPIO.kext', message)
            self.assertIn('"binary_version": "1.0.0d1"', message)
            self.assertIn('"plist_version": "1.1"', message)
            self.assertIn('"expected_identifier": "org.coolstar.VoodooGPIO"', message)
            self.assertIn('"binary_identifier": "org.coolstar.VoodooGPIO"', message)

    def test_rejects_nested_plugins(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = synthetic_bundle(Path(temporary))
            (bundle / 'Contents/PlugIns').mkdir()
            with self.assertRaises(ValueError):
                build_i2c.validate_bundle(bundle)

    def test_rejects_gpu_dependency(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            build_i2c.validate_bundle(synthetic_bundle(Path(temporary), libraries={'de.nordapps.LunarBridge': '0.2.0d1'}))

    def test_native_build_cannot_run_on_windows(self):
        with patch.object(build_i2c.platform, 'system', return_value='Windows'), self.assertRaises(ValueError):
            build_i2c.build(Path('not accessed'), Path('not created'))

    def test_build_output_cannot_contaminate_prepared_source(self):
        destination = PREPARED / 'unexpected-build-output'
        with patch.object(build_i2c.platform, 'system', return_value='Darwin'), \
             patch.object(build_i2c.platform, 'machine', return_value='x86_64'), self.assertRaises(ValueError):
            build_i2c.build(PREPARED, destination)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', required=True)
    args = parser.parse_args()
    PREPARED = Path(args.prepared).resolve(strict=True)
    suite = unittest.TestSuite((unittest.defaultTestLoader.loadTestsFromTestCase(PreparationChecks),
                               unittest.defaultTestLoader.loadTestsFromTestCase(PackageRejectionChecks)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'test_type': 'Host preparation/security checks with synthetic rejection fixtures; no macOS hardware tests',
              'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
              'successful': result.wasSuccessful(), 'native_hardware_tested': False}
    (PREPARED.parent / 'i2c-build-test-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
