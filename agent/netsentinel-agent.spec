# PyInstaller spec for the standalone NetSentinel agent.
#
# Build from the REPO ROOT so `src` and `agent` both resolve:
#     pyinstaller agent/netsentinel-agent.spec --noconfirm
#
# Produces one self-contained executable with no Python, no git and no pip
# needed on the target machine.
import os

block_cipher = None

# agent/agent.py imports `collector` and `agent_paths` as top-level modules
# (it puts its own directory on sys.path), and collector imports `src.*`.
# Both directories therefore have to be on PyInstaller's analysis path.
PATHS = ["agent", "."]

# Imported dynamically or via a path PyInstaller cannot follow statically.
HIDDEN = [
    "collector",
    "agent_paths",
    "src.gateway_monitor",
    "src.connectivity",
    "src.interface_monitor",
    "src.dns_monitor",
    "src.port_checker",
    "src.logger",
]

# The server stack has no business in an agent binary: it more than triples
# the size and ships code the agent never calls.
EXCLUDED = [
    "fastapi", "uvicorn", "starlette", "supabase", "gotrue", "postgrest",
    "realtime", "storage3", "supafunc", "pytest", "reportlab", "pypdfium2",
    "numpy", "pandas", "matplotlib", "PIL", "tkinter",
]

a = Analysis(
    ["agent.py"],
    pathex=PATHS,
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=os.environ.get("AGENT_BINARY_NAME", "netsentinel-agent"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # A console app on purpose: first run asks for the enrolment code, and a
    # windowless binary would have nowhere to ask. The autostart installers
    # launch it via pythonw/systemd where no window appears anyway.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
