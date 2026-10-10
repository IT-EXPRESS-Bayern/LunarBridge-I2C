# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 IT-EXPRESS Bayern and LunarBridge contributors.
"""Prepare a pinned, separate I2C build; no driver loading or firmware writes."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / 'build-manifest.json').read_text(encoding='utf-8'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_git(git, root, *args):
    return subprocess.check_output([git, '-C', str(root), *args], stderr=subprocess.PIPE)


def verify_checkout(git, root, name):
    root = root.resolve(strict=True)
    top = Path(run_git(git, root, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if top != root:
        raise ValueError('Expected an independent Git checkout: ' + name)
    commit = run_git(git, root, 'rev-parse', 'HEAD').decode().strip()
    if commit != MANIFEST['sources'][name]['commit']:
        raise ValueError('Unexpected source commit: ' + name)
    if run_git(git, root, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Tracked source modifications are not accepted: ' + name)
    return root


def extract_archive(data, destination):
    """Extract bounded Git archives without traversal, links or private task files."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 6000 or sum(item.file_size for item in members) > 128 * 1024 * 1024:
            raise ValueError('Source archive exceeds its bound')
        for item in members:
            raw_name = item.orig_filename
            path = PurePosixPath(raw_name)
            mode = item.external_attr >> 16
            if path.is_absolute() or any(x in ('', '.', '..') for x in path.parts) or \
               '\\' in raw_name or ':' in raw_name or '\0' in raw_name or stat.S_ISLNK(mode):
                raise ValueError('Unsafe source archive member')
            if path.name.lower() in ('tasks.md', 'plan.md'):
                continue
            target = destination.joinpath(*path.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                if mode & stat.S_IXUSR:
                    target.chmod(target.stat().st_mode | stat.S_IXUSR)


def replace_once(text, pattern, replacement, label):
    result, count = re.subn(pattern, replacement, text, flags=re.S)
    if count != 1:
        raise ValueError('Pinned project structure mismatch: ' + label)
    return result


def align_project_versions(text, alignment):
    """Keep the declared bundle version and make generated kmod metadata agree."""
    original = alignment['original_project_version']
    version = alignment['bundle_version']
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}', version):
        raise ValueError('Unexpected pinned bundle version')
    for setting in ('CURRENT_PROJECT_VERSION', 'MODULE_VERSION'):
        pattern = r'(?m)^(\s*' + setting + r' = )' + re.escape(original) + r';$'
        text, count = re.subn(pattern, lambda match: match.group(1) + version + ';', text)
        if count != 2:
            raise ValueError('Pinned Debug/Release version structure mismatch: ' + setting)
    return text


def patch_project(text, *, sdk_library, core=False, input_project=False):
    if core:
        text = replace_once(text, r'(?<=buildPhases = \()(.*?)(?=\n\s*\);)',
                            lambda m: '\n'.join(line for line in m.group(0).split('\n')
                                                if 'Add GPIO and Services Plugin' not in line and 'Archive' not in line),
                            'core packaging phases')
        text = replace_once(text, r'dependencies = \(.*?\);', 'dependencies = ();', 'core target dependencies')
        text = replace_once(text, r'projectReferences = \(.*?\);', 'projectReferences = ();', 'core project references')
    if input_project:
        text = replace_once(text, r'(?<=buildPhases = \()(.*?)(?=\n\s*\);)',
                            lambda m: '\n'.join(line for line in m.group(0).split('\n') if 'Archive' not in line),
                            'input archive phase')
    quoted = json.dumps(str(sdk_library).replace('\\', '/'))
    text = replace_once(text, r'path = [^;]*libkmod\.a; sourceTree = "<group>";',
                        'path = ' + quoted + '; sourceTree = "<group>";', 'kernel module library')
    return text


def prepare(source, input_source, sdk, output, git='git'):
    source = verify_checkout(git, Path(source), 'VoodooI2C')
    input_source = verify_checkout(git, Path(input_source), 'VoodooInput')
    sdk = verify_checkout(git, Path(sdk), 'MacKernelSDK')
    submodules = {}
    for name in ('VoodooGPIO', 'VoodooI2CHID'):
        location = source / MANIFEST['sources'][name]['submodule']
        submodules[name] = verify_checkout(git, location, name)
        tree = run_git(git, source, 'ls-tree', 'HEAD', MANIFEST['sources'][name]['submodule']).decode()
        if ('160000 commit ' + MANIFEST['sources'][name]['commit']) not in tree:
            raise ValueError('Unexpected upstream submodule pin: ' + name)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Build preparation requires a new output directory')
    if any(output == root or root.is_relative_to(output) or output.is_relative_to(root)
           for root in (source, input_source, sdk)):
        raise ValueError('Output must be separate from source checkouts')
    # No preexisting checkout is modified. Git archives preserve exact LF source bytes on Windows.
    output.mkdir(parents=True)
    roots = {'VoodooI2C': output / 'VoodooI2C', 'VoodooInput': output / 'VoodooInput',
             'MacKernelSDK': output / 'MacKernelSDK'}
    for name, root in (('VoodooI2C', source), ('VoodooInput', input_source), ('MacKernelSDK', sdk)):
        extract_archive(run_git(git, root, '-c', 'core.autocrlf=false', 'archive', '--format=zip', 'HEAD'), roots[name])
    for name, root in submodules.items():
        extract_archive(run_git(git, root, '-c', 'core.autocrlf=false', 'archive', '--format=zip', 'HEAD'),
                        roots['VoodooI2C'] / MANIFEST['sources'][name]['submodule'])
    original_info = roots['VoodooI2C'] / 'VoodooI2C/VoodooI2C/Info.plist'
    patch_info = json.loads((HERE / 'patch-info.json').read_text(encoding='utf-8'))
    if digest(original_info.read_bytes()) != patch_info['original_sha256']:
        raise ValueError('Unexpected original I2C metadata')
    patched = (HERE / 'Info.plist').read_bytes()
    if digest(patched) != patch_info['patched_sha256']:
        raise ValueError('Unexpected patched I2C metadata')
    original_info.write_bytes(patched)
    changes = {}
    for relative, expected_hash in MANIFEST['project_sha256'].items():
        project = (roots['VoodooInput'] if relative.startswith('VoodooInput.xcodeproj')
                   else roots['VoodooI2C']) / relative
        data = project.read_bytes()
        if digest(data) != expected_hash:
            raise ValueError('Unexpected upstream Xcode project: ' + relative)
        library_relative = Path(os.path.relpath(roots['MacKernelSDK'] / 'Library/x86_64/libkmod.a', project.parent.parent))
        modified = patch_project(data.decode('utf-8'), sdk_library=library_relative,
                                 core=relative == 'VoodooI2C/VoodooI2C.xcodeproj/project.pbxproj',
                                 input_project=relative.startswith('VoodooInput.xcodeproj'))
        alignment = MANIFEST['project_version_alignment'].get(relative)
        if alignment:
            info = plistlib.loads((roots['VoodooI2C'] / alignment['info_plist']).read_bytes())
            if info.get('CFBundleVersion') != alignment['bundle_version']:
                raise ValueError('Pinned bundle version mismatch: ' + relative)
            modified = align_project_versions(modified, alignment)
        project.write_text(modified, encoding='utf-8', newline='\n')
        changes[relative] = {'original_sha256': expected_hash, 'prepared_sha256': digest(project.read_bytes())}
        if alignment:
            changes[relative]['version_alignment'] = alignment
    # The unchanged upstream source set and original license texts accompany each driver.
    report = {'schema': 1, 'source_pins': MANIFEST['sources'], 'project_changes': changes,
              'patch_sha256': digest((HERE / 'VoodooI2C-LunarLake-Samsung.patch').read_bytes()),
              'patched_info_sha256': digest(patched), 'independent_kexts': list(MANIFEST['expected_bundles']),
              'gpio_lunar_lake_supported': False, 'native_hardware_tested': False,
              'driver_loaded': False, 'firmware_secure_boot_changed': False}
    (output / 'preparation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'prepared': True, 'kexts': report['independent_kexts'], 'hardware_tested': False}))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--input-source', required=True)
    parser.add_argument('--sdk', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--git', default='git')
    args = parser.parse_args()
    try:
        prepare(args.source, args.input_source, args.sdk, args.output, args.git)
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(2, 'I2C build preparation failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
