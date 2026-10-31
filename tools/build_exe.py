"""Builds MassLeveler.exe (the dashboard) in the MassLeveler folder with PyInstaller.

Run after changing anything in manager/:   py -I tools/build_exe.py
The exe reads settings.json, accounts.csv, data/, templates/ and plugin/ from the folder it sits in, and the
"Rebuild skill plans" button still runs tools/make_builds.py from disk, so only manager/ code is inside the exe.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = 'MassLeveler Escort'     # its own name: the original MassLeveler.exe stays a different program


def make_icon(path):
    """dark square with a blue up arrow (level up)"""
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), radius=48, fill=(30, 30, 30, 255), outline=(14, 99, 156, 255), width=10)
    d.polygon([(128, 40), (210, 130), (160, 130), (160, 216), (96, 216), (96, 130), (46, 130)], fill=(100, 181, 246, 255))
    img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main():
    work = tempfile.mkdtemp(prefix='_build_', dir=ROOT)      # same drive as the exe, so it can be moved in place
    icon = os.path.join(work, 'massleveler.ico')
    try:
        make_icon(icon)
        icon_args = ['--icon', icon]
    except ImportError:
        icon_args = []
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--name', NAME,
           '--paths', os.path.join(ROOT, 'manager'),
           # used by tools/make_builds.py, which the dashboard loads from disk
           '--hidden-import', 'argparse', '--hidden-import', 'glob', '--hidden-import', 'sqlite3',
           '--distpath', os.path.join(work, 'dist'), '--workpath', os.path.join(work, 'build'),
           '--specpath', work] + icon_args + [os.path.join(ROOT, 'manager', 'mass_leveler.py')]
    exe, old = os.path.join(ROOT, NAME + '.exe'), os.path.join(ROOT, NAME + '.old.exe')
    try:
        subprocess.run(cmd, check=True)
        if os.path.isfile(old):
            try:
                os.remove(old)
            except OSError:
                pass
        try:
            os.replace(os.path.join(work, 'dist', NAME + '.exe'), exe)
        except PermissionError:
            # the dashboard is open: Windows lets a running exe be renamed, the new one is used from its next start
            os.replace(exe, old if not os.path.isfile(old) else exe[:-4] + '.old%d.exe' % os.getpid())
            os.replace(os.path.join(work, 'dist', NAME + '.exe'), exe)
            print('MassLeveler is open - the new exe is used the next time you open it')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('Built', exe)


if __name__ == '__main__':
    main()
