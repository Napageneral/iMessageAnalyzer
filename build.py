import os
import shutil
import subprocess
import glob

def clean_build():
    folders_to_remove = ['build', 'dist']
    for folder in folders_to_remove:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"Removed {folder} folder")

def run_pyinstaller():
    command = [
        "pyinstaller",
        "--name=iMessageAnalyzer",
        "--windowed",
        "--onefile",
        "--clean",
        "--add-data=assets:assets",
        "--add-data=script.py:.",
        "--icon=assets/icon.icns",
        "--hidden-import=sqlite3",
        "--hidden-import=fuzzywuzzy",
        "app.py"
    ]
    subprocess.run(command, check=True)

def create_dmg():
    # Find the .app file
    app_files = glob.glob("dist/*.app")
    if not app_files:
        print("No .app file found in the dist directory.")
        return
    
    app_path = app_files[0]
    dmg_name = os.path.basename(app_path).replace('.app', '.dmg')
    
    command = [
        "hdiutil",
        "create",
        "-volname", "iMessage Analyzer",
        "-srcfolder", app_path,
        "-size", "200m",  # Allocate 200MB for the DMG
        "-ov",
        "-format", "UDZO",
        dmg_name
    ]
    try:
        subprocess.run(command, check=True)
        print(f"DMG created successfully: {dmg_name}")
    except subprocess.CalledProcessError as e:
        print(f"Error creating DMG: {e}")

if __name__ == "__main__":
    clean_build()
    run_pyinstaller()
    create_dmg()  # Only on macOS