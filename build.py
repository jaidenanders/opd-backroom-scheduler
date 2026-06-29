"""
Build script for OPD Backroom Scheduler.

Usage:
    python build.py           # folder build (default)
    python build.py --onefile # single-file .exe
    python build.py --clean   # wipe dist/ and build/ first

Requires: pip install pyinstaller
"""
import subprocess
import sys
import shutil
import argparse
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description="Build BackroomScheduler executable")
    parser.add_argument("--onefile", action="store_true",
                        help="Produce a single .exe instead of a folder")
    parser.add_argument("--clean", action="store_true",
                        help="Delete dist/ and build/ before building")
    args = parser.parse_args()

    if args.clean:
        for d in ("dist", "build"):
            target = ROOT / d
            if target.exists():
                shutil.rmtree(target)
                print(f"Removed {target}")

    # Patch the spec to honour --onefile flag at build time
    spec_src = (ROOT / "backroom_scheduler.spec").read_text()
    if args.onefile:
        spec_src = spec_src.replace("onefile = False", "onefile = True", 1)
    tmp_spec = ROOT / "_build_tmp.spec"
    tmp_spec.write_text(spec_src)

    cmd = [sys.executable, "-m", "PyInstaller", str(tmp_spec), "--noconfirm"]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    tmp_spec.unlink(missing_ok=True)

    if result.returncode != 0:
        print("\nBuild FAILED.")
        sys.exit(result.returncode)

    out = ROOT / "dist" / "BackroomScheduler"
    if args.onefile:
        out = ROOT / "dist" / "BackroomScheduler.exe"
    print(f"\nBuild complete: {out}")


if __name__ == "__main__":
    main()
