#!/usr/bin/env python3
"""Prepare a relocatable universal framework from the verified PSF installer."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '.build-support'
VERSION = '3.14.9'
INSTALLER_URL = f'https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-macos11.pkg'
INSTALLER_SHA256 = '08e312f20844b524c2f8c5e5b2e7f49608c22e8c7cb194f5834d385c94383462'
FRAMEWORK_PREFIX = '/Library/Frameworks/Python.framework/'


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    installer = OUTPUT / f'python-{VERSION}-macos11.pkg'
    if not installer.exists():
        subprocess.run(['curl', '--fail', '--location', '--proto', '=https',
                        '--tlsv1.2', '--output', str(installer), INSTALLER_URL], check=True)
    if hashlib.sha256(installer.read_bytes()).hexdigest() != INSTALLER_SHA256:
        raise SystemExit('Python installer SHA-256 mismatch')
    signature = run('pkgutil', '--check-signature', str(installer))
    if 'Python Software Foundation (BMM5U3QVKW)' not in signature:
        raise SystemExit('Python installer is not signed by the PSF')
    run('spctl', '--assess', '--type', 'install', str(installer))

    archive = OUTPUT / f'Python-{VERSION}-macOS-support.tar.gz'
    with tempfile.TemporaryDirectory(prefix='switch-python-support-') as temporary:
        temporary = Path(temporary)
        expanded = temporary / 'installer'
        run('pkgutil', '--expand-full', str(installer), str(expanded))
        source = expanded / 'Python_Framework.pkg/Payload'
        if not (source / 'Versions/3.14/Python').is_file():
            raise SystemExit('Expected one Python framework in the installer')
        staging = temporary / 'support'
        framework = staging / 'Python.xcframework/macos-arm64_x86_64/Python.framework'
        shutil.copytree(source, framework, symlinks=True)
        version = framework / 'Versions/3.14'
        library = version / 'Python'
        if set(run('lipo', '-archs', str(library)).split()) != {'arm64', 'x86_64'}:
            raise SystemExit('Python framework is not universal')
        if not any(line == VERSION or line.startswith(VERSION + ' ') for line in run('strings', str(library)).splitlines()):
            raise SystemExit('Unexpected embedded Python version')
        # Package installation, Tcl/Tk and the interpreter's own app launcher
        # are not used by the embedded Toga application.
        for relative in ['lib/python3.14/ensurepip', 'lib/python3.14/test',
                         'lib/python3.14/site-packages', 'lib/python3.14/idlelib',
                         'lib/python3.14/tkinter', 'Resources/Python.app', 'bin', 'Frameworks']:
            path = version / relative
            if path.exists():
                shutil.rmtree(path)
        for path in (version / 'lib/python3.14/lib-dynload').glob('_tkinter*'):
            path.unlink()
        # PSF frameworks use absolute install names. Resolve every dependency
        # inside this framework through the app launcher's Frameworks rpath.
        for path in framework.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as handle:
                magic = handle.read(4)
            if magic not in [bytes.fromhex(value) for value in
                             ['cffaedfe', 'feedfacf', 'cafebabe', 'bebafeca']]:
                continue
            dependencies = run('otool', '-L', str(path))
            changes = []
            for line in dependencies.splitlines():
                if not line.startswith('\t'):
                    continue
                dependency = line.strip().split(' (', 1)[0]
                if dependency.startswith(FRAMEWORK_PREFIX):
                    changes += ['-change', dependency,
                                '@rpath/Python.framework/' + dependency[len(FRAMEWORK_PREFIX):]]
            if path == library or path.suffix == '.dylib':
                changes += ['-id', '@rpath/Python.framework/' + str(path.relative_to(framework))]
            if changes:
                run('install_name_tool', *changes, str(path))
            run('codesign', '--force', '--sign', '-', '--timestamp=none', str(path))
        run('codesign', '--force', '--deep', '--sign', '-', '--timestamp=none', str(framework))
        run('codesign', '--verify', '--deep', '--strict', str(framework))
        with tarfile.open(archive, 'w:gz') as output:
            output.add(staging / 'Python.xcframework', arcname='Python.xcframework')
    print(f'Prepared PSF Python {VERSION} universal framework: {archive.name}')


if __name__ == '__main__':
    prepare()
