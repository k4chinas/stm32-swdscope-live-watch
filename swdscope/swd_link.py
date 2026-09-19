from __future__ import annotations

import atexit
import os
import socket
import struct
import subprocess
import time
import glob
import shutil

EXE = ".exe" if os.name == "nt" else ""

CUBEIDE_KOKLERI = (
    "C:/ST/STM32CubeIDE*/STM32CubeIDE",
    "/opt/st/stm32cubeide*",
    "/Applications/STM32CubeIDE.app/Contents/Eclipse",
)
EKLENTI = "/plugins/com.st.stm32cube.ide.mcu.externaltools."


def _cubeide_ara(eklenti, alt_yol):
    bulunan = []
    for kok in CUBEIDE_KOKLERI:
        bulunan += glob.glob(kok + EKLENTI + eklenti + "*/tools/bin" + alt_yol)
    if not bulunan:
        return None
    return sorted(bulunan)[-1].replace("\\", "/")


def _bul_gdbserver():
    if "SWD_GDBSERVER" in os.environ:
        return os.environ["SWD_GDBSERVER"]
    yol = _cubeide_ara("stlink-gdb-server", "/ST-LINK_gdbserver" + EXE)
    if yol:
        return yol
    return shutil.which("ST-LINK_gdbserver") or "ST-LINK_gdbserver" + EXE


def _bul_cubeprog():
    if "SWD_CUBEPROG" in os.environ:
        return os.environ["SWD_CUBEPROG"]
    for aday in ("C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin",
                 "/opt/st/STM32CubeProgrammer/bin"):
        if os.path.isdir(aday):
            return aday
    yol = _cubeide_ara("cubeprogrammer", "")
    if yol:
        return yol
    cli = shutil.which("STM32_Programmer_CLI")
    if cli:
        return os.path.dirname(cli)
    return "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin"


GDBSERVER = _bul_gdbserver()

CUBEPROG = _bul_cubeprog()

PORT = int(os.environ.get("SWD_GDB_PORT", "61240"))


def elf_symbols(path):
    with open(path, "rb") as fh:
        data = fh.read()

    if data[:4] != bytes([0x7F]) + b"ELF":
        raise ValueError("ELF dosyasi degil: " + path)
    if data[4] != 1 or data[5] != 1:
        raise ValueError("sadece 32 bit little-endian ELF destekleniyor")

    e_shoff, = struct.unpack_from("<I", data, 0x20)
    e_shentsize, e_shnum = struct.unpack_from("<HH", data, 0x2E)

    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sections.append(struct.unpack_from("<IIIIIII", data, off))

    SHT_SYMTAB = 2
    symtab = next((sec for sec in sections if sec[1] == SHT_SYMTAB), None)
    if symtab is None:
        raise ValueError(".symtab yok - dosya strip edilmis olabilir")

    sym_off, sym_size, strtab_idx = symtab[4], symtab[5], symtab[6]
    str_off = sections[strtab_idx][4]

    out = {}
    NUL = bytes(1)
    for off in range(sym_off, sym_off + sym_size, 16):
        st_name, st_value, st_size = struct.unpack_from("<III", data, off)
        if st_name == 0:
            continue
        end = data.index(NUL, str_off + st_name)
        name = data[str_off + st_name:end].decode("utf-8", "replace")
        if name:
            out[name] = (st_value, st_size)
    return out


def _rle_coz(data: bytes) -> bytes:
    if b"*" not in data:
        return data
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x2A and out and i + 1 < len(data):
            out += bytes([out[-1]]) * (data[i + 1] - 29)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


class Rsp:
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
        self.no_ack = False

    def _byte(self) -> bytes:
        if not self.buf:
            chunk = self.sock.recv(8192)
            if not chunk:
                raise ConnectionError("gdbserver baglantiyi kapatti")
            self.buf = chunk
        b, self.buf = self.buf[:1], self.buf[1:]
        return b

    def _send(self, payload: bytes) -> None:
        csum = sum(payload) & 0xFF
        self.sock.sendall(b"$" + payload + b"#" + f"{csum:02x}".encode())
        if not self.no_ack:
            while True:
                b = self._byte()
                if b == b"+":
                    break
                if b == b"-":
                    raise RuntimeError("paket reddedildi")

    def _recv(self) -> bytes:
        while self._byte() != b"$":
            pass
        data = b""
        while True:
            b = self._byte()
            if b == b"#":
                break
            data += b
        self._byte()
        self._byte()
        if not self.no_ack:
            self.sock.sendall(b"+")
        return _rle_coz(data)

    def cmd(self, payload: bytes) -> bytes:
        self._send(payload)
        return self._recv()

    def start_no_ack(self) -> None:
        try:
            if self.cmd(b"QStartNoAckMode") == b"OK":
                self.no_ack = True
        except (OSError, RuntimeError):
            pass

    def resume(self) -> bool:
        try:
            if self.cmd(b"QNonStop:1") != b"OK":
                return False
            return self.cmd(b"vCont;c") == b"OK"
        except (OSError, RuntimeError):
            return False

    def read_mem(self, addr: int, length: int) -> bytes:
        r = self.cmd(f"m{addr:x},{length:x}".encode())
        if r[:1] == b"E":
            raise RuntimeError(f"bellek okunamadi @0x{addr:08X}: {r.decode(errors='replace')}")
        try:
            veri = bytes.fromhex(r.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"gecersiz yanit @0x{addr:08X}: {r[:40]!r}") from exc
        if len(veri) != length:
            raise RuntimeError(f"eksik yanit @0x{addr:08X}: {len(veri)}/{length} bayt")
        return veri

    def write_mem(self, addr: int, data: bytes) -> None:
        payload = f"M{addr:x},{len(data):x}:".encode() + data.hex().encode()
        r = self.cmd(payload)
        if r != b"OK":
            raise RuntimeError("bellege yazilamadi @0x%08X: %s"
                               % (addr, r.decode(errors="replace")))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def connect_rsp(port: int, timeout: float) -> "Rsp":
    end = time.time() + timeout
    last = None
    while True:
        try:
            return Rsp("127.0.0.1", port)
        except OSError as exc:
            last = exc
            if time.time() >= end:
                raise ConnectionError(f"gdbserver'a baglanilamadi: {last}")
            time.sleep(0.3)

def _kapat(server) -> None:
    try:
        if server.poll() is None:
            server.terminate()
    except OSError:
        pass


def open_link(port: int = PORT, timeout: float = 20.0):
    server = None
    try:
        rsp = connect_rsp(port, timeout=0.6)
    except ConnectionError:
        server = subprocess.Popen(
            [GDBSERVER, "-p", str(port), "-d", "-g", "-cp", CUBEPROG],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        atexit.register(_kapat, server)
        try:
            rsp = connect_rsp(port, timeout=timeout)
        except ConnectionError:
            _kapat(server)
            raise

    rsp.start_no_ack()
    rsp.resume()
    return rsp, server
