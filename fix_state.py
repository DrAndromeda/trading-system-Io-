from pathlib import Path
p = Path("daily_report.py")
src = p.read_text()

# Заменяем обращение к state["first_run"] на .get()
old = '''    state = load_state()
    if state["first_run"] is None:
        state["first_run"] = now.isoformat()
    state["runs"] = state.get("runs", 0) + 1'''

new = '''    state = load_state()
    if not state.get("first_run"):
        state["first_run"] = now.isoformat()
    if "signals" not in state or not isinstance(state["signals"], list):
        state["signals"] = []
    state["runs"] = state.get("runs", 0) + 1'''

if old in src:
    src = src.replace(old, new)
    p.write_text(src)
    print("OK: fixed")
else:
    print("WARN: anchor not found, patching directly")

    # fallback: прямое
    src = src.replace('if state["first_run"] is None:', 'if not state.get("first_run"):')
    src = src.replace('state["runs"] = state.get("runs", 0) + 1',
                      'state["runs"] = state.get("runs", 0) + 1\n    if "signals" not in state: state["signals"] = []')
    p.write_text(src)
    print("OK: fallback applied")
