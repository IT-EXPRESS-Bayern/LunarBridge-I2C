# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
"""Build and package five independent upstream-derived I2C kexts on macOS."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'tools'))
from macho import inspect_macho
from prepare_build import MANIFEST

TARGETS = (
    ('VoodooGPIO', 'VoodooI2C/Dependencies/VoodooGPIO/VoodooGPIO.xcodeproj'),
    ('VoodooI2CServices', 'VoodooI2C/Dependencies/VoodooI2CServices/VoodooI2CServices.xcodeproj'),
    ('VoodooI2C', 'VoodooI2C/VoodooI2C/VoodooI2C.xcodeproj'),
    ('VoodooI2CHID', 'VoodooI2C/VoodooI2C Satellites/VoodooI2CHID/VoodooI2CHID.xcodeproj'),
    ('VoodooInput', 'VoodooInput/VoodooInput.xcodeproj'),
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_bundle(bundle):
    """Check a real kernel Mach-O, identity and separate packaging; never load it."""
    if bundle.name not in MANIFEST['expected_bundles'] or bundle.is_symlink():
        raise ValueError('Unexpected kernel bundle')
    if (bundle / 'Contents/PlugIns').exists():
        raise ValueError('Nested kext plug-ins are not accepted in the separate I2C package')
    files = list(bundle.rglob('*'))
    if len(files) > 256 or any(path.is_symlink() for path in files):
        raise ValueError('Unexpected bundle layout')
    info_path = bundle / 'Contents/Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    expected = MANIFEST['expected_bundles'][bundle.name]
    if info.get('CFBundleIdentifier') != expected or info.get('CFBundlePackageType') != 'KEXT':
        raise ValueError('Kernel bundle identity mismatch')
    executable = info.get('CFBundleExecutable', '')
    if not executable or executable != Path(executable).name or '/' in executable or '\\' in executable:
        raise ValueError('Invalid kernel executable name')
    binary = bundle / 'Contents/MacOS' / executable
    data = binary.read_bytes()
    if len(data) > 32 * 1024 * 1024:
        raise ValueError('Unexpected kernel executable size')
    metadata = inspect_macho(data, require_kext=True)
    if metadata['identifier'] != expected or metadata['version'] != info.get('CFBundleVersion'):
        fields = {'expected_identifier': expected, 'binary_identifier': metadata['identifier'],
                  'plist_version': info.get('CFBundleVersion'), 'binary_version': metadata['version']}
        controlled = {key: value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value)
                      else '<invalid>' for key, value in fields.items()}
        raise ValueError('Mach-O kernel metadata does not match Info.plist: ' +
                         bundle.name + ' ' + json.dumps(controlled, sort_keys=True))
    if metadata['has_code_signature']:
        raise ValueError('Build is expected to be unsigned; signing requires a separate qualified policy')
    libraries = info.get('OSBundleLibraries', {})
    if not isinstance(libraries, dict) or 'de.nordapps.LunarBridge' in libraries:
        raise ValueError('I2C drivers must not depend on the GPU driver')
    if bundle.name == 'VoodooI2C.kext':
        personality = info['IOKitPersonalities'].get('VoodooI2CPCILunarLakeSamsungController', {})
        if personality.get('IOPCIPrimaryMatch') != '0xa8788086 0xa8798086 0xa87a8086 0xa87b8086' or \
           personality.get('IOPCISecondaryMatch') != '0xc1dd144d':
            raise ValueError('Samsung-only experimental controller personality missing')
    if bundle.name == 'VoodooGPIO.kext':
        experimental = info.get('IOKitPersonalities', {}).get('VoodooGPIOSamsungLunarLake', {})
        if experimental.get('LunarBridgeGPIOEnabled') is not False or \
           experimental.get('IONameMatch') != ['INTC105D', 'INTC105F'] or \
           experimental.get('IOProviderClass') != 'IOACPIPlatformDevice' or \
           experimental.get('IOClass') != 'VoodooGPIOSamsungLunarLake' or \
           not any('VoodooGPIOSamsungLunarLake' in name for name in metadata['defined_symbols']):
            raise ValueError('Compiled experimental GPIO implementation missing or enabled by default')
    return {'bundle': bundle.name, 'identifier': expected, 'version': metadata['version'],
            'architecture': metadata['architecture'], 'filetype': metadata['filetype'],
            'binary_sha256': sha256(binary), 'binary_bytes': len(data), 'info_sha256': sha256(info_path),
            'libraries': libraries, 'unsigned': True, 'native_hardware_tested': False}


def source_files(prepared):
    """Only the pinned source snapshots, never Git metadata or private work files."""
    for source_name in ('VoodooI2C', 'VoodooInput'):
        directory = prepared / source_name
        for path in sorted(directory.rglob('*')):
            if path.is_dir():
                continue
            relative = path.relative_to(directory)
            if path.is_symlink() or '.git' in relative.parts:
                raise ValueError('Unexpected source link or Git metadata')
            if relative.name.lower() in ('tasks.md', 'plan.md'):
                continue
            yield path, 'source/' + source_name + '/' + relative.as_posix()


def package(prepared, products, destination, report):
    sdk = prepared / 'MacKernelSDK'
    linked_library = sdk / MANIFEST['sources']['MacKernelSDK']['linked_library']
    if sha256(linked_library) != MANIFEST['sources']['MacKernelSDK']['linked_library_sha256']:
        raise ValueError('Linked external kernel-module library hash mismatch')
    sdk_source_paths = [sdk / 'LICENSE.txt', sdk / 'README.md', *sorted((sdk / 'Library/kmod').rglob('*'))]
    sdk_source_paths = [path for path in sdk_source_paths if path.is_file()]
    if not any(path.name == 'c_start.c' for path in sdk_source_paths):
        raise ValueError('External kernel-module library source notices are missing')
    infos = [validate_bundle(products / name) for name in MANIFEST['expected_bundles']]
    report = dict(report, bundles=infos, independently_packaged=True,
                  driver_loaded=False, native_hardware_tested=False, gpio_lunar_lake_supported=False,
                  firmware_secure_boot_tested=False, gpu_driver_required=False)
    report['linked_sdk_library'] = {'repository': 'acidanthera/MacKernelSDK',
        'commit': MANIFEST['sources']['MacKernelSDK']['commit'], 'path': 'Library/x86_64/libkmod.a',
        'sha256': sha256(linked_library), 'bytes': linked_library.stat().st_size,
        'license': 'Apple Public Source License 2.0; upstream notices retained, not GPL relicensed',
        'license_archive_path': 'source/MacKernelSDK/LICENSE.txt', 'available_source_archive_path': 'source/MacKernelSDK/Library/kmod',
        'prebuilt_library_rebuilt_from_source_and_compared': False}
    # This is a disabled configuration fragment, not an EFI edit or an installer.
    load_order = ('VoodooGPIO.kext', 'VoodooI2CServices.kext', 'VoodooInput.kext', 'VoodooI2C.kext', 'VoodooI2CHID.kext')
    kernel_add = []
    for name in load_order:
        info = plistlib.loads((products / name / 'Contents/Info.plist').read_bytes())
        kernel_add.append({'Arch': 'x86_64', 'BundlePath': name, 'Comment': 'Experimental Samsung I2C; target validation pending',
                           'Enabled': False, 'ExecutablePath': 'Contents/MacOS/' + info['CFBundleExecutable'],
                           'PlistPath': 'Contents/Info.plist', 'MinKernel': '25.0.0', 'MaxKernel': '25.99.99'})
    report_path = destination / 'i2c-build-report.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    archive_path = destination / 'LunarBridge-I2C-Samsung-Experimental.zip'
    source_manifest = {}
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in MANIFEST['expected_bundles']:
            bundle = products / name
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    archive.write(path, 'Kexts/' + name + '/' + path.relative_to(bundle).as_posix())
        for path, name in source_files(prepared):
            archive.write(path, name)
            source_manifest[name] = sha256(path)
        for path in sdk_source_paths:
            if path.is_symlink() or path.name.lower() in ('tasks.md', 'plan.md'):
                raise ValueError('Unexpected external library source layout')
            name = 'source/MacKernelSDK/' + path.relative_to(sdk).as_posix()
            archive.write(path, name)
            source_manifest[name] = sha256(path)
        for path in sorted(HERE.glob('*')):
            if path.is_file() and path.suffix in ('.py', '.json', '.plist', '.patch') and path.name != 'test-report.json':
                name = 'source/LunarBridge/i2c-patch/' + path.name
                archive.write(path, name)
                source_manifest[name] = sha256(path)
        for path in sorted((ROOT / 'gpio-patch').glob('*')):
            if path.is_file() and path.suffix in ('.py', '.cpp', '.hpp', '.json', '.patch'):
                name = 'source/LunarBridge/gpio-patch/' + path.name
                archive.write(path, name)
                source_manifest[name] = sha256(path)
        for relative in ('tools/macho.py', 'LICENSE', 'NOTICE', 'README.md'):
            path, name = ROOT / relative, 'source/LunarBridge/' + relative
            archive.write(path, name)
            source_manifest[name] = sha256(path)
        for name in ('I2C_BACKEND.md', 'GPIO_PORT.md'):
            archive.write(ROOT / 'docs' / name, name)
            source_manifest[name] = sha256(ROOT / 'docs' / name)
        archive.write(report_path, report_path.name)
        archive.writestr('OpenCore-Disabled-I2C-Fragment.plist', plistlib.dumps({'Kernel': {'Add': kernel_add}}, sort_keys=False))
        archive.writestr('SOURCE-HASHES.json', json.dumps(source_manifest, indent=2) + '\n')
        archive.writestr('SOURCE-NOTICES.txt',
            'Experimental Samsung I2C source matching changed by IT-EXPRESS Bayern on 2026-10-10.\n'
            'Original VoodooI2C, VoodooI2CHID and VoodooGPIO copyrights/licenses remain intact.\n'
            'VoodooInput is a separately built, unchanged GPLv2 upstream component; it is not relicensed.\n'
            'Build sources and original license texts are in source/. Build scripts reproduce the changes.\n'
            'External system SDK: acidanthera/MacKernelSDK at ' + MANIFEST['sources']['MacKernelSDK']['commit'] + '.\n'
            'SDK libkmod.a is linked into these kexts; its Apple APSL license and available module source are included.\n'
            'See SOURCE-NOTICES-SDK.txt and source/MacKernelSDK/. No SDK component is relicensed.\n'
            'Each Kexts/ bundle remains independent. Experimental Samsung GPI1 GPIO code is compiled but disabled by default.\n'
            'GPIO matching requires explicit opt-in, the narrow Samsung PCI identity and the exact live ACPI layout.\n'
            'Unsigned CI build. No macOS target loading, GPIO, HID or UEFI Secure Boot test has passed.\n')
        archive.writestr('SOURCE-NOTICES-SDK.txt',
            'Linked external kernel-module library: acidanthera/MacKernelSDK\n'
            'Pinned commit: ' + MANIFEST['sources']['MacKernelSDK']['commit'] + '\n'
            'Library: Library/x86_64/libkmod.a\nSHA-256: ' + sha256(linked_library) + '\n'
            'License: original Apple Public Source License 2.0 in source/MacKernelSDK/LICENSE.txt.\n'
            'Original Apple and other file-specific copyright notices are retained in available source.\n'
            'Included available module sources: source/MacKernelSDK/Library/kmod/.\n'
            'SDK provenance and build instructions: source/MacKernelSDK/README.md.\n'
            'The prebuilt SDK library has not been rebuilt and compared byte-for-byte with that source subtree.\n'
            'This package does not claim that unrelated SDK headers or the static archive are GPL-licensed.\n')
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None or any(Path(name).name.lower() in ('tasks.md', 'plan.md') for name in archive.namelist()):
            raise ValueError('Packaged source validation failed')
    (destination / (archive_path.name + '.sha256')).write_text(sha256(archive_path) + '  ' + archive_path.name + '\n', encoding='ascii')
    return report


def build(prepared, destination):
    if platform.system() != 'Darwin' or platform.machine() != 'x86_64':
        raise ValueError('A real Intel macOS/Xcode build host is required')
    prepared = Path(prepared).resolve(strict=True)
    preparation = json.loads((prepared / 'preparation.json').read_text(encoding='utf-8'))
    if preparation['source_pins'] != MANIFEST['sources']:
        raise ValueError('Prepared sources differ from the pinned manifest')
    destination = Path(destination).resolve()
    if destination.exists() or destination == prepared or prepared.is_relative_to(destination) or destination.is_relative_to(prepared):
        raise ValueError('Build destination must be a new directory outside the prepared sources')
    destination.mkdir(parents=True)
    sdk_headers = prepared / 'MacKernelSDK/Headers'
    sdk_library = prepared / 'MacKernelSDK/Library/x86_64'
    products = destination / 'products/Release'
    settings = ['ARCHS=x86_64', 'ONLY_ACTIVE_ARCH=NO', 'CODE_SIGNING_ALLOWED=NO', 'CODE_SIGNING_REQUIRED=NO',
                'CODE_SIGN_IDENTITY=', 'ENABLE_USER_SCRIPT_SANDBOXING=NO', 'MACOSX_DEPLOYMENT_TARGET=12.0',
                'GCC_TREAT_WARNINGS_AS_ERRORS=NO', 'DEBUG_INFORMATION_FORMAT=dwarf',
                'GCC_GENERATE_DEBUGGING_SYMBOLS=NO', 'LD_NO_PIE=YES',
                'KERNEL_EXTENSION_HEADER_SEARCH_PATHS=' + str(sdk_headers),
                'KERNEL_FRAMEWORK_HEADERS=' + str(sdk_headers),
                'LIBRARY_SEARCH_PATHS=' + str(sdk_library),
                'HEADER_SEARCH_PATHS=' + str(prepared / 'VoodooInput/VoodooInput'),
                'SYMROOT=' + str(destination / 'products'), 'OBJROOT=' + str(destination / 'intermediates')]
    for target, project in TARGETS:
        project_path = prepared / project
        if not project_path.is_dir():
            raise ValueError('Prepared project missing: ' + target)
        subprocess.run(['xcodebuild', '-project', str(project_path), '-target', target, '-configuration', 'Release',
                        '-jobs', '1', *settings, 'build'], cwd=project_path.parent, check=True)
        validate_bundle(products / (target + '.kext'))
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    report = {'schema': 1, 'source_commit': commit, 'source_pins': MANIFEST['sources'], 'configuration': 'Release',
              'compiler': subprocess.check_output(['xcrun', 'clang', '--version'], text=True).splitlines()[0],
              'build_host': {'system': 'macOS', 'architecture': 'x86_64',
                             'version': subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip()},
              'preparation': preparation}
    report = package(prepared, products, destination, report)
    print(json.dumps({'built_kexts': len(report['bundles']), 'native_hardware_tested': False,
                      'firmware_secure_boot_tested': False, 'independently_packaged': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        build(args.prepared, args.output)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(2, 'Independent I2C build failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
