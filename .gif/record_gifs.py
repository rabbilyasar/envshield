#!/usr/bin/env python3
"""
Helper script to record and convert GIFs for EnvShield documentation.

Usage:
    python3 record_gifs.py              # Record all GIFs
    python3 record_gifs.py import       # Record just import.gif
    python3 record_gifs.py import setup # Record import and setup GIFs
"""

import os
import subprocess
import sys
from pathlib import Path

GIF_DIR = Path(__file__).parent
SCRIPTS = {
    "import": ("record-import.sh", "import"),
    "setup": ("record-setup.sh", "setup"),
    "multi-service": ("record-multi-service.sh", "multi-service"),
    "doctor": ("record-doctor.sh", "doctor"),
    "scan": ("record-scan.sh", "scan"),
}

def check_dependencies():
    """Verify asciinema and agg are installed."""
    try:
        subprocess.run(["asciinema", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ asciinema not found. Install with: pip install asciinema")
        return False

    try:
        subprocess.run(["agg", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ agg not found. Install with: pip install agg")
        return False

    return True

def record_gif(script_name: str, gif_name: str):
    """Record a single GIF using asciinema."""
    script_path = GIF_DIR / script_name
    cast_path = GIF_DIR / f"{gif_name}.cast"
    gif_path = GIF_DIR / f"{gif_name}.gif"

    if not script_path.exists():
        print(f"❌ Script not found: {script_path}")
        return False

    # Make script executable
    os.chmod(script_path, 0o755)

    print(f"\n🎬 Recording {gif_name}.gif...")
    try:
        # Record with asciinema - use environment variables for terminal size
        env = os.environ.copy()
        env['COLUMNS'] = '100'
        env['LINES'] = '28'

        cmd = [
            "asciinema", "rec",
            "-c", f"bash {script_path}",
            str(cast_path)
        ]
        subprocess.run(cmd, env=env, check=True)

        # Convert to GIF with slower playback (0.7x speed = slower)
        print("🎨 Converting to GIF (slowed for readability)...")
        cmd = [
            "agg",
            str(cast_path),
            str(gif_path),
            "--speed", "0.7"  # Slow down to 70% speed (30% slower)
        ]
        subprocess.run(cmd, check=True)

        # Clean up cast file
        cast_path.unlink()

        print(f"✅ Created {gif_path}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error recording {gif_name}: {e}")
        return False

def main():
    if not check_dependencies():
        sys.exit(1)

    # Determine which GIFs to record
    if len(sys.argv) > 1:
        gifs_to_record = sys.argv[1:]
    else:
        gifs_to_record = list(SCRIPTS.keys())

    # Validate GIF names
    for gif_name in gifs_to_record:
        if gif_name not in SCRIPTS:
            print(f"❌ Unknown GIF: {gif_name}")
            print(f"   Available: {', '.join(SCRIPTS.keys())}")
            sys.exit(1)

    # Record each GIF
    results = {}
    for gif_name in gifs_to_record:
        script_name, _ = SCRIPTS[gif_name]
        success = record_gif(script_name, gif_name)
        results[gif_name] = success

    # Summary
    print("\n" + "="*50)
    print("GIF Recording Summary:")
    print("="*50)
    for gif_name, success in results.items():
        status = "✅" if success else "❌"
        print(f"{status} {gif_name}.gif")

    all_success = all(results.values())
    if all_success:
        print("\n🎉 All GIFs recorded successfully!")
        print(f"📁 Location: {GIF_DIR}")
    else:
        print("\n⚠️  Some GIFs failed to record.")
        sys.exit(1)

if __name__ == "__main__":
    main()
