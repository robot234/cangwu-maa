"""Release the Agent's device lock when its verified MXU owner exits."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading


def find_owner(pid: int, processes: dict[int, tuple[int, str]]) -> int | None:
    seen = set()
    for _ in range(12):
        if pid in seen or pid not in processes:
            return None
        seen.add(pid)
        parent, _ = processes[pid]
        if parent in processes and processes[parent][1].casefold() == "mxu.exe":
            return parent
        pid = parent
    return None


def monitor_owner(stop, probe, exit_process) -> None:
    while not stop.wait(.25):
        status = probe()
        if status == 0:  # WAIT_OBJECT_0: this exact process handle has exited.
            exit_process(0)
            return
        if status != 258:  # Never treat an unknown wait failure as owner exit.
            return


@contextmanager
def watch_mxu_owner(root: Path, emit=print):
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("name", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise RuntimeError("无法核对助手主进程，未启动执行进程。")
    processes, entry = {}, Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            processes[entry.pid] = (entry.parent, entry.name)
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    owner = find_owner(os.getpid(), processes)
    if owner is None:
        raise RuntimeError("未能确认本项目的助手主窗口，未启动执行进程。")
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, owner)
    if not handle:
        raise RuntimeError("无法关联助手主窗口，未启动执行进程。")
    stop, thread = threading.Event(), None
    try:
        image, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if (not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length))
                or Path(image.value).resolve() != (root / "app/mxu.exe").resolve()
                or kernel.WaitForSingleObject(handle, 0) != 258):
            raise RuntimeError("助手主窗口身份或运行状态未核对，未启动执行进程。")
        # An open handle follows the original process even if its PID is reused.
        # The owner is already gone when _exit is called: durable spend ledgers
        # remain intact, and Windows releases this Agent's lock and IPC handles.
        thread = threading.Thread(target=monitor_owner,
            args=(stop, lambda: kernel.WaitForSingleObject(handle, 0), os._exit),
            name="CangwuMxuOwner", daemon=True)
        thread.start()
        emit(f"执行进程已关联助手主窗口（PID {owner}），窗口退出后自动释放设备锁。")
        yield
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=1)
        kernel.CloseHandle(handle)
