"""Identify the process that actually owns the local MXU HTTP listener."""
from urllib.parse import urlsplit
import socket


def select_listener_pid(api: str, listeners: list[tuple[str, int, int]]) -> int:
    address = urlsplit(api)
    if (address.scheme != "http" or address.hostname != "127.0.0.1" or address.path != "/api"
            or address.username is not None or address.password is not None
            or address.query or address.fragment or address.port is None
            or not 12701 <= address.port <= 12710):
        raise RuntimeError("助手重启仅允许本机已发现的项目接口。")
    exact = {pid for host, port, pid in listeners if host == "127.0.0.1" and port == address.port}
    candidates = exact or {pid for host, port, pid in listeners if host == "0.0.0.0" and port == address.port}
    if len(candidates) != 1:
        raise RuntimeError("无法唯一确认助手接口对应的进程，拒绝关闭窗口。")
    return candidates.pop()


def listener_pid(api: str) -> int:
    import ctypes
    from ctypes import wintypes

    class Row(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in ("state", "local_address", "local_port",
                                                     "remote_address", "remote_port", "pid")]

    get_table = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    get_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                         wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    get_table.restype = wintypes.DWORD
    size = wintypes.DWORD()
    result = get_table(None, ctypes.byref(size), False, socket.AF_INET, 3, 0)
    if result not in (0, 122):
        raise RuntimeError(f"读取助手接口所属进程失败：{result}")
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = get_table(buffer, ctypes.byref(size), False, socket.AF_INET, 3, 0)
        if result == 122:
            continue
        if result != 0:
            raise RuntimeError(f"读取助手接口所属进程失败：{result}")
        count = wintypes.DWORD.from_buffer(buffer).value
        if 4 + count * ctypes.sizeof(Row) > len(buffer):
            raise RuntimeError("助手接口进程表长度无效。")
        rows = [Row.from_buffer_copy(buffer, 4 + index * ctypes.sizeof(Row)) for index in range(count)]
        listeners = [(socket.inet_ntoa(int(row.local_address).to_bytes(4, "little")),
                      socket.ntohs(row.local_port & 0xffff), row.pid) for row in rows if row.state == 2]
        return select_listener_pid(api, listeners)
    raise RuntimeError("助手接口所属进程持续变化，拒绝关闭窗口。")
