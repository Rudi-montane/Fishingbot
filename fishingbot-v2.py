import asyncio  
import pymem
import time
import keyboard
import psutil
import pyautogui
import win32gui
import win32process
import win32con
import win32api
import tkinter as tk
import queue
import threading

#prozess namen xxxxx 64 bit 
PROCESS_NAME = "xxxxxxx.exe"

# pointer chain für casten status # is_casting / casting 1 // nicht 0
addresses_zero = (
    "lotroclient64.exe",  # name des modules
    0x019A0DA8,  # speicheradresse des modules
    [0x18, 0x3D0, 0x48, 0xC0, 0x28, 0x80, 0x258]  # offsets aktive adresse
)

# pointer chain für fisch gebissen # fisch gebissen 1 // nicht 0
addresses_one = (
    "lotroclient64.exe",  # name des modules
    0x01DCB200,  # pointer
    [0x54, 0x20, 0xA0, 0x10, 0x20, 0x30]  # offsets aktiver adresse 
)

# taste für fishingskill 
KEY_TO_PRESS = "2"

# verschiedene wartezeiten in sekunden
SHORT_SLEEP = 0.05  # kurze pause für humanlike behavior
ACTION_DELAY = 1.2  # delay nach aktion (spam schutz)
FOCUS_SLEEP = 0.05  # zeit nach fokussieren lotro
COOLDOWN = 2       # cooldown nach aktion/key press (spam schutz)
STABLE_DURATION = 0.1  # wie lange ein wert stabil sein muss
CHECK_INTERVAL = 0.02  # intervall für das überprüfen von werten

# globale variablen zur steuerung
active_event = asyncio.Event()  # bot aktiv oder pausiert
active_event.set()

debug_queue = queue.Queue()  # thread-sichere debug queue
asyncio_loop = None  # referenz zur asyncio event loop

def debug_log(message):
    timestamp = time.strftime("%H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    debug_queue.put(full_message)
    print(full_message)

def find_all_instances():
    instances = []
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            if proc.info["name"].lower() == PROCESS_NAME.lower():
                instances.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return instances

def get_window_by_pid(pid):
    target_windows = []
    def enum_handler(hwnd, lParam):
        try:
            if win32gui.IsWindowVisible(hwnd):
                _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                if found_pid == pid:
                    target_windows.append(hwnd)
        except Exception:
            pass
    win32gui.EnumWindows(enum_handler, None)
    return target_windows

def focus_lotro_window(hwnd):
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, 9)  # 9 für wiederherstellen
        if win32gui.GetForegroundWindow() != hwnd:
            win32gui.SetForegroundWindow(hwnd)
        time.sleep(FOCUS_SLEEP)
        rect = win32gui.GetWindowRect(hwnd)
        x = rect[0] + 50
        y = rect[1] + 10
        pyautogui.click(x, y)
        return True
    except Exception as e:
        debug_log(f"fokus fehlgeschlagen: {e}")
        return False

def read_pointer_chain(pm, pointer_chain):
    try:
        if len(pointer_chain) == 3:
            module_name, module_offset, offsets = pointer_chain
            module = pymem.process.module_from_name(pm.process_handle, module_name)
            if not module:
                debug_log(f"modul {module_name} nicht gefunden")
                return None
            base_address = module.lpBaseOfDll + module_offset
        elif len(pointer_chain) == 2:
            base_address, offsets = pointer_chain
        else:
            debug_log("ungültiges pointer format")
            return None

        address = pm.read_int(base_address)
        for offset in offsets:
            address = pm.read_int(address + offset)
        return address
    except Exception as e:
        debug_log(f"fehler beim lesen der pointer chain: {e}")
        return None

def read_addresses(pm, addr_spec):
    values = []
    if isinstance(addr_spec, tuple):
        final_addr = read_pointer_chain(pm, addr_spec)
        if final_addr is not None:
            try:
                value = pm.read_int(final_addr)
                debug_log(f"zeigerkette aufgeloest zu {hex(final_addr)} mit wert {value}")
                values.append(value)
            except Exception as e:
                debug_log(f"fehler beim lesen an {hex(final_addr)}: {e}")
                values.append(None)
        else:
            values.append(None)
        return values
    for addr in addr_spec:
        try:
            value = pm.read_int(addr)
            debug_log(f"adresse {hex(addr)} hat wert {value}")
            values.append(value)
        except Exception as e:
            debug_log(f"fehler beim lesen der adresse {hex(addr)}: {e}")
            values.append(None)
    return values

def all_values_equal(values, target):
    return all(val == target for val in values if val is not None)

async def wait_for_stable_state(pm, addr_spec, expected_value, loop):
    stable_start = None
    while True:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = loop
        values = await current_loop.run_in_executor(None, read_addresses, pm, addr_spec)
        if all_values_equal(values, expected_value):
            if stable_start is None:
                stable_start = time.time()
            elif time.time() - stable_start >= STABLE_DURATION:
                return True
        else:
            stable_start = None
        await asyncio.sleep(CHECK_INTERVAL)

async def fishing_bot(pid, loop):
    debug_log(f"starte fischbot für prozess {pid}...")
    try:
        pm = pymem.Pymem()
        pm.open_process_from_id(pid)
    except Exception as e:
        debug_log(f"fehler beim öffnen des prozesses {pid}: {e}")
        return

    hwnds = get_window_by_pid(pid)
    if not hwnds:
        debug_log(f"[pid {pid}] kein fenster gefunden")
        return
    hwnd = hwnds[0]
    last_key_press_time = 0
    while True:
        await active_event.wait()
        current_time = time.time()
        if current_time - last_key_press_time < COOLDOWN:
            await asyncio.sleep(SHORT_SLEEP)
            continue
        debug_log(f"[pid {pid}] warte bis casten wert 0 ist")
        await wait_for_stable_state(pm, addresses_zero, 0, loop)
        if focus_lotro_window(hwnd):
            debug_log(f"[pid {pid}] drücke taste {KEY_TO_PRESS}")
            keyboard.press_and_release(KEY_TO_PRESS)
            last_key_press_time = time.time()
            await asyncio.sleep(ACTION_DELAY)
        debug_log(f"[pid {pid}] warte auf fisch bissen")
        await wait_for_stable_state(pm, addresses_one, 1, loop)
        debug_log(f"[pid {pid}] fisch hat gebissen")
        if focus_lotro_window(hwnd):
            debug_log(f"[pid {pid}] drücke taste {KEY_TO_PRESS}")
            keyboard.press_and_release(KEY_TO_PRESS)
            debug_log("eingezogen")
            last_key_press_time = time.time()
            await asyncio.sleep(ACTION_DELAY)
        await asyncio.sleep(SHORT_SLEEP)

class FishingBotGUI:
    def __init__(self, root):
        self.root = root
        root.title("Rudi_Montane LotRO Fishing Bot")
        
        self.instance_label = tk.Label(root, text="lotro instanzen: 0")
        self.instance_label.pack(pady=5)
        
        self.start_button = tk.Button(root, text="bot starten", command=self.start_bot, width=20)
        self.start_button.pack(pady=5)
        
        self.stop_button = tk.Button(root, text="bot stoppen", command=self.stop_bot, width=20)
        self.stop_button.pack(pady=5)
        
        self.debug_text = tk.Text(root, height=20, width=80)
        self.debug_text.pack(pady=5)
        
        self.update_debug_console()
        self.update_instance_count()
        
    def start_bot(self):
        active_event.set()
        debug_log("bot gestartet")
        
    def stop_bot(self):
        active_event.clear()
        debug_log("bot gestoppt")
        
    def update_debug_console(self):
        while not debug_queue.empty():
            try:
                message = debug_queue.get_nowait()
            except queue.Empty:
                break
            self.debug_text.insert(tk.END, message + "\n")
            self.debug_text.see(tk.END)
        self.root.after(100, self.update_debug_console)
        
    def update_instance_count(self):
        count = len(find_all_instances())
        self.instance_label.config(text=f"lotro instanzen: {count}")
        self.root.after(1000, self.update_instance_count)

def run_asyncio():
    global asyncio_loop
    asyncio_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(asyncio_loop)
    try:
        instances = find_all_instances()
        loop = asyncio_loop
        tasks = [asyncio.create_task(fishing_bot(pid, loop)) for pid in instances]
        if tasks:
            asyncio_loop.run_until_complete(asyncio.gather(*tasks))
        else:
            debug_log("keine lotro instanzen gefunden")
    except Exception as e:
        debug_log(f"asyncio schleife fehler: {e}")
    finally:
        asyncio_loop.close()

def main():
    asyncio_thread = threading.Thread(target=run_asyncio, daemon=True)
    asyncio_thread.start()
    
    root = tk.Tk()
    gui = FishingBotGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()
