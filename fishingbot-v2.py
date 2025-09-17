# -*- coding: utf-8 -*-
"""
LOTRO Fishing Bot — headless
============================

Small script that watches two memory flags and taps your fishing key at the right time.

What it does
------------
- Finds all running 'lotroclient64.exe' processes
- Per process:
  - wait until "not casting" (flag == 0) -> press fishing key (cast)
  - wait until "fish bitten" (flag == 1) -> press fishing key again (reel)

Hotkeys
-------
F6  = resume
F7  = pause
Ctrl+C to quit

Requirements
------------
Python 3.10+ on Windows, plus:
    pip install pymem psutil keyboard pyautogui pywin32

Notes
-----
- Run as Administrator.
- Offsets break when the game updates; adjust the pointer chains below.
- Automating a game can violate the game's ToS. Use at your own risk.
"""

import asyncio
import time
import pymem
import psutil
import keyboard
import pyautogui
import win32gui
import win32process

__version__ = "0.2.0"

# --- Config -----------------------------------------------------------------

# 64-bit client process name
PROCESS_NAME = "lotroclient64.exe"

# Casting flag: 1 = casting, 0 = idle
addresses_zero = (
    "lotroclient64.exe",           # module
    0x019A0DA8,                    # module-relative ptr
    [0x18, 0x3D0, 0x48, 0xC0, 0x28, 0x80, 0x258]  # pointer path
)

# Fish bitten flag: 1 = bite, 0 = no bite
addresses_one = (
    "lotroclient64.exe",           # module
    0x01DCB200,                    # module-relative ptr
    [0x54, 0x20, 0xA0, 0x10, 0x20, 0x30]          # pointer path
)

# Key bound to your fishing skill in-game
KEY_TO_PRESS = "2"

# Timing (seconds)
SHORT_SLEEP     = 0.05
ACTION_DELAY    = 1.2
FOCUS_SLEEP     = 0.05
COOLDOWN        = 2.0
STABLE_DURATION = 0.10
CHECK_INTERVAL  = 0.02

# Global on/off switch (starts enabled)
active_event = asyncio.Event()
active_event.set()


# --- Logging ----------------------------------------------------------------

def debug_log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# --- Process / Window helpers -----------------------------------------------

def find_all_instances() -> list[int]:
    """Return PIDs of all running LOTRO client instances."""
    pids = []
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            if (n := proc.info["name"]) and n.lower() == PROCESS_NAME.lower():
                pids.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids


def get_windows_by_pid(pid: int) -> list[int]:
    """Return visible top-level window handles for the PID."""
    handles: list[int] = []

    def enum_handler(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                if found_pid == pid:
                    handles.append(hwnd)
        except Exception:
            pass

    win32gui.EnumWindows(enum_handler, None)
    return handles


def focus_window(hwnd: int) -> bool:
    """
    Bring the window to the foreground and click into it.
    Keeps keystrokes going to the right place.
    """
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
        if win32gui.GetForegroundWindow() != hwnd:
            win32gui.SetForegroundWindow(hwnd)
        time.sleep(FOCUS_SLEEP)

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        pyautogui.click(left + 50, top + 10)
        return True
    except Exception as e:
        debug_log(f"Focus failed: {e}")
        return False


# --- Memory helpers ----------------------------------------------------------

def resolve_ptr(pm: pymem.Pymem, spec) -> int | None:
    """
    Resolve a (module + base_ptr + offsets) chain to a final address.
    Returns None if anything goes wrong.
    """
    try:
        if len(spec) == 3:
            module_name, module_off, offsets = spec
            module = pymem.process.module_from_name(pm.process_handle, module_name)
            if not module:
                debug_log(f"Module not found: {module_name}")
                return None
            base_ptr_addr = module.lpBaseOfDll + module_off
            addr = pm.read_int(base_ptr_addr)
        elif len(spec) == 2:
            base_addr, offsets = spec
            addr = pm.read_int(base_addr)
        else:
            debug_log("Bad pointer spec")
            return None

        for off in offsets:
            addr = pm.read_int(addr + off)

        return addr
    except Exception as e:
        debug_log(f"Ptr resolve error: {e}")
        return None


def read_int(pm: pymem.Pymem, spec) -> int | None:
    """Read final int value from a pointer spec."""
    final = resolve_ptr(pm, spec)
    if final is None:
        return None
    try:
        val = pm.read_int(final)
        debug_log(f"{hex(final)} -> {val}")
        return val
    except Exception as e:
        debug_log(f"Read failed at {hex(final)}: {e}")
        return None


async def wait_stable(pm: pymem.Pymem, spec, expected: int) -> None:
    """
    Wait until value == expected and stays there for STABLE_DURATION.
    Keeps polling on a thread to avoid blocking the event loop.
    """
    stable_since = None
    loop = asyncio.get_running_loop()

    while True:
        val = await loop.run_in_executor(None, read_int, pm, spec)

        if val == expected:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= STABLE_DURATION:
                return
        else:
            stable_since = None

        await asyncio.sleep(CHECK_INTERVAL)


# --- Worker ------------------------------------------------------------------

async def fishing_bot(pid: int) -> None:
    """Cast → wait → reel loop for one LOTRO PID."""
    debug_log(f"[PID {pid}] worker up")

    try:
        pm = pymem.Pymem()
        pm.open_process_from_id(pid)
    except Exception as e:
        debug_log(f"[PID {pid}] open failed: {e}")
        return

    hwnds = get_windows_by_pid(pid)
    if not hwnds:
        debug_log(f"[PID {pid}] no window found")
        return
    hwnd = hwnds[0]

    last_press = 0.0

    while True:
        await active_event.wait()

        if time.time() - last_press < COOLDOWN:
            await asyncio.sleep(SHORT_SLEEP)
            continue

        # Wait until not casting
        debug_log(f"[PID {pid}] wait: casting == 0")
        await wait_stable(pm, addresses_zero, expected=0)

        if focus_window(hwnd):
            debug_log(f"[PID {pid}] cast ({KEY_TO_PRESS})")
            keyboard.press_and_release(KEY_TO_PRESS)
            last_press = time.time()
            await asyncio.sleep(ACTION_DELAY)

        # Wait for bite
        debug_log(f"[PID {pid}] wait: bite == 1")
        await wait_stable(pm, addresses_one, expected=1)

        if focus_window(hwnd):
            debug_log(f"[PID {pid}] reel ({KEY_TO_PRESS})")
            keyboard.press_and_release(KEY_TO_PRESS)
            last_press = time.time()
            await asyncio.sleep(ACTION_DELAY)

        await asyncio.sleep(SHORT_SLEEP)


# --- Hotkeys -----------------------------------------------------------------

def _resume():
    active_event.set()
    debug_log("resume (F6)")

def _pause():
    active_event.clear()
    debug_log("pause (F7)")


# --- Entrypoint --------------------------------------------------------------

async def async_main():
    keyboard.add_hotkey("F6", _resume)
    keyboard.add_hotkey("F7", _pause)

    pids = find_all_instances()
    if not pids:
        debug_log("No LOTRO instances found.")
        return

    debug_log(f"PIDs: {pids}")
    tasks = [asyncio.create_task(fishing_bot(pid)) for pid in pids]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        debug_log("bye")