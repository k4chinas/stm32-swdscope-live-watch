from __future__ import annotations

import ast
import json
import os
import re
import struct
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Final, Iterator, TextIO

import dwarf
from swd_link import CUBEPROG, elf_symbols, open_link


TYPES: Final[dict[str, tuple[str, int]]] = {
    "u8": ("<B", 1),
    "i8": ("<b", 1),
    "u16": ("<H", 2),
    "i16": ("<h", 2),
    "u32": ("<I", 4),
    "i32": ("<i", 4),
    "u64": ("<Q", 8),
    "i64": ("<q", 8),
    "f32": ("<f", 4),
    "f64": ("<d", 8),
}

SIZE_DEFAULT: Final[dict[int, str]] = {1: "u8", 2: "u16", 4: "u32", 8: "f64"}

MAX_BLOCK: Final[int] = 512

CPUID_ADRES: Final[int] = 0xE000ED00

WATCHDOG_S: Final[float] = 2.0

RAM_START: Final[int] = 0x20000000
RAM_END: Final[int] = 0x20040000

RETRY_INTERVAL_S: Final[float] = 2.0
RETRY_MAX: Final[int] = 10
RETRY_CONNECT_TIMEOUT_S: Final[float] = 3.0

ELF_GECMIS_SINIRI: Final[int] = 5

ELF_DENEME: Final[int] = 3
ELF_BEKLEME_S: Final[float] = 0.4

PROFILE_FILE: Final[str] = "swdscope_profil.json"
FORMAT_VERSION: Final[int] = 1


class ElfHatasi(RuntimeError):
    pass


class ProbeError(RuntimeError):
    pass


@dataclass(slots=True)
class VarSpec:
    name: str
    type: str = "u32"
    addr: int = 0

    @property
    def size(self) -> int:
        return TYPES[self.type][1]


@dataclass(slots=True)
class Sample:
    seq: int
    t: float
    values: dict[str, float | int]


@dataclass(slots=True)
class ProbeInfo:
    serial: str
    firmware: str = ""
    device: str = ""
    voltage: str = ""

    @property
    def label(self) -> str:
        parts = [self.device or "STM32", f"SN {self.serial[:12]}"]
        if self.voltage:
            parts.append(self.voltage)
        return "  ".join(parts)


_SN_RE = re.compile(r"ST-LINK SN\s*:\s*(\S+)")
_FW_RE = re.compile(r"ST-LINK FW\s*:\s*(\S+)")
_DEV_RE = re.compile(r"Device name\s*:\s*(.+)")
_VOLT_RE = re.compile(r"Voltage\s*:\s*(\S+)")

_CLI = str(Path(CUBEPROG) / ("STM32_Programmer_CLI" + (".exe" if os.name == "nt" else "")))


def _run_cli(args: list[str], timeout: float = 25.0) -> str:
    try:
        done = subprocess.run(
            [_CLI, *args], capture_output=True, text=True, timeout=timeout
        )
        return done.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_probe() -> ProbeInfo | None:
    out = _run_cli(["-l"])
    sn = _SN_RE.search(out)
    if not sn:
        return None
    fw = _FW_RE.search(out)
    return ProbeInfo(serial=sn.group(1), firmware=fw.group(1) if fw else "")


def identify_target(probe: ProbeInfo) -> ProbeInfo:
    out = _run_cli(["-c", "port=SWD", "mode=HOTPLUG"])
    dev = _DEV_RE.search(out)
    volt = _VOLT_RE.search(out)
    if dev:
        probe.device = dev.group(1).strip()
    if volt:
        probe.voltage = volt.group(1).strip()
    return probe


class ProbeWatcher:
    def __init__(self, on_change: Callable[[ProbeInfo | None], None],
                 interval: float = 2.0) -> None:
        self._on_change = on_change
        self._interval = interval
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._last: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._last = None
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._paused.is_set():
                probe = detect_probe()
                key = probe.serial if probe else None
                if key != self._last:
                    self._last = key
                    self._on_change(probe)
            self._stop.wait(self._interval)


@dataclass
class Profile:
    elf: str = ""
    variables: list[VarSpec] = field(default_factory=list)
    hz: float = 50.0
    out_dir: str = "logs"
    trigger: str = ""
    pre: int = 200
    post: int = 200
    rearm: bool = True
    max_captures: int = 0
    son_elfler: list[str] = field(default_factory=list)

    def save(self, path: str | Path = PROFILE_FILE) -> Path:
        p = Path(path)
        data = asdict(self)
        data["variables"] = [
            {"name": v.name, "type": v.type, "addr": v.addr}
            for v in self.variables
        ]
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path = PROFILE_FILE) -> "Profile":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        prof = cls(
            elf=raw.get("elf", ""),
            hz=float(raw.get("hz", 50.0)),
            out_dir=raw.get("out_dir", "logs"),
            trigger=raw.get("trigger", ""),
            pre=int(raw.get("pre", 200)),
            post=int(raw.get("post", 200)),
            rearm=bool(raw.get("rearm", True)),
            max_captures=int(raw.get("max_captures", 0)),
            son_elfler=[y for y in raw.get("son_elfler", [])
                        if isinstance(y, str)][:ELF_GECMIS_SINIRI],
        )
        for item in raw.get("variables", []):
            typ = item.get("type", "u32")
            if typ in TYPES:
                prof.variables.append(
                    VarSpec(name=item["name"], type=typ, addr=int(item.get("addr", 0)))
                )
        return prof


_ELF_ONBELLEK: dict = {}


def elf_degiskenleri(elf: str) -> dict[str, tuple[int, int, str | None]]:
    try:
        anahtar = (elf, Path(elf).stat().st_mtime_ns)
    except OSError:
        anahtar = (elf, 0)
    if anahtar in _ELF_ONBELLEK:
        return _ELF_ONBELLEK[anahtar]

    son_hata = None
    semboller = None
    for deneme in range(ELF_DENEME):
        try:
            semboller = elf_symbols(elf)
            break
        except (OSError, ValueError, IndexError, struct.error) as exc:
            son_hata = exc
            if deneme < ELF_DENEME - 1:
                time.sleep(ELF_BEKLEME_S)

    if semboller is None:
        raise ElfHatasi(
            "%s okunamadı — dosya şu anda yazılıyor olabilir (CubeIDE "
            "derliyorsa derleme bitince tekrar dene). Ayrıntı: %s"
            % (Path(elf).name, son_hata))

    birlesik: dict[str, tuple[int, int, str | None]] = {}
    for isim, (adres, boyut) in semboller.items():
        birlesik[isim] = (adres, boyut, None)

    try:
        for isim, (adres, tip) in dwarf.degisken_tipleri(elf).items():
            birlesik[isim] = (adres, TYPES[tip][1], tip)
    except (dwarf.DwarfHatasi, OSError, ValueError, IndexError, KeyError,
            struct.error):
        pass

    _ELF_ONBELLEK.clear()
    _ELF_ONBELLEK[anahtar] = birlesik
    return birlesik


def elf_gecmisine_ekle(profil, yol: str) -> None:
    yol = (yol or "").strip()
    if not yol:
        return
    gecmis = [y for y in profil.son_elfler if y != yol]
    gecmis.insert(0, yol)
    profil.son_elfler = gecmis[:ELF_GECMIS_SINIRI]


def list_candidates(elf: str) -> list[tuple[int, int, str, str | None]]:
    rows = [
        (addr, size, name, tip)
        for name, (addr, size, tip) in elf_degiskenleri(elf).items()
        if RAM_START <= addr < RAM_END and size > 0
    ]
    rows.sort()
    return rows


def default_type_for(size: int) -> str:
    return SIZE_DEFAULT.get(size, "u32")


class Sampler:
    def __init__(self, variables: list[VarSpec], elf: str = "", hz: float = 50.0) -> None:
        if not variables:
            raise ValueError("izlenecek değişken seçilmedi")
        self.variables = variables
        self.elf = elf
        self.hz = hz
        self._rsp = None
        self._server = None
        self._block: tuple[int, int] | None = None
        self._kilit = threading.Lock()

    def _adresleri_coz(self) -> None:
        if self.elf:
            syms = elf_degiskenleri(self.elf)
            eksik = [v.name for v in self.variables if v.name not in syms]
            if eksik:
                raise RuntimeError(
                    "%s içinde bulunamayan semboller: %s" % (self.elf, ", ".join(eksik))
                )
            for v in self.variables:
                v.addr = syms[v.name][0]

        eksik_adres = [v.name for v in self.variables if not v.addr]
        if eksik_adres:
            raise RuntimeError(
                "adresi bilinmeyen değişkenler: %s" % ", ".join(eksik_adres)
            )

        lo = min(v.addr for v in self.variables)
        hi = max(v.addr + v.size for v in self.variables)
        self._block = (lo, hi - lo) if (hi - lo) <= MAX_BLOCK else None

    def degiskenleri_degistir(self, variables) -> None:
        if not variables:
            raise ValueError("izlenecek değişken seçilmedi")
        with self._kilit:
            self.variables = list(variables)
            self._adresleri_coz()

    def open(self, connect_timeout: float | None = None,
             tanila: bool = True) -> None:
        self._adresleri_coz()
        self._rsp, self._server = self._baglan(connect_timeout, tanila)

        if not self.hat_canli():
            self.close()
            raise ProbeError(
                "gdbserver acildi ama kart yanit vermiyor - ST-LINK "
                "takili degil ya da hedef beslenmiyor")

    def hat_canli(self) -> bool:
        if self._rsp is None:
            return False
        try:
            with self._kilit:
                ham = self._rsp.read_mem(CPUID_ADRES, 4)
        except (OSError, RuntimeError, ValueError, AttributeError):
            return False
        deger = struct.unpack("<I", ham)[0]
        return deger not in (0x00000000, 0xFFFFFFFF)

    def _baglan(self, connect_timeout=None, tanila=True):
        try:
            if connect_timeout is None:
                return open_link()
            return open_link(timeout=connect_timeout)
        except (OSError, RuntimeError) as exc:
            if not tanila:
                raise ProbeError(str(exc)) from exc
            if detect_probe() is None:
                raise ProbeError(
                    "Kart bulunamadi - ST-LINK takili degil. USB kablosunu "
                    "kontrol et."
                ) from exc
            raise ProbeError(
                "ST-LINK gorunuyor ama baglanilamadi. CubeIDE'de debug oturumu "
                "ya da CubeMonitor acik olabilir; probe tek istemci kabul eder. "
                "Ayrinti: %s" % exc
            ) from exc

    def close(self) -> None:
        with self._kilit:
            if self._rsp is not None:
                self._rsp.close()
                self._rsp = None
            if self._server is not None:
                self._server.terminate()
                self._server = None

    def __enter__(self) -> "Sampler":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def read_once(self) -> dict[str, float | int]:
        with self._kilit:
            if self._rsp is None:
                raise RuntimeError("Sampler açık değil")
            return self._oku()

    def _oku(self) -> dict[str, float | int]:
        out: dict[str, float | int] = {}
        if self._block is not None:
            base, span = self._block
            blob = self._rsp.read_mem(base, span)
            for v in self.variables:
                out[v.name] = struct.unpack_from(TYPES[v.type][0], blob, v.addr - base)[0]
        else:
            for v in self.variables:
                raw = self._rsp.read_mem(v.addr, v.size)
                out[v.name] = struct.unpack(TYPES[v.type][0], raw)[0]
        return out

    def _yeniden_baglan(self, butce: int, on_event) -> int:
        self.close()
        son_hata: Exception | None = None
        baslangic = time.perf_counter()
        sonraki = baslangic

        for deneme in range(1, butce + 1):
            sonraki += RETRY_INTERVAL_S
            bekle = sonraki - time.perf_counter()
            if bekle > 0:
                time.sleep(bekle)
            try:
                self.open(connect_timeout=RETRY_CONNECT_TIMEOUT_S, tanila=False)
            except (OSError, RuntimeError) as exc:
                son_hata = exc
                if on_event:
                    on_event("deniyor", "%d/%d: %s" % (deneme, butce, exc))
                continue
            if on_event:
                on_event("baglandi", "%d. denemede baglanildi" % deneme)
            return deneme

        mesaj = ("%d denemede (%.0f saniye) baglanilamadi, vazgecildi"
                 % (butce, time.perf_counter() - baslangic))
        if on_event:
            on_event("vazgecildi", mesaj)
        raise ProbeError("%s. Son hata: %s" % (mesaj, son_hata))

    def yaz(self, isim: str, deger) -> float | int:
        with self._kilit:
            v = next((x for x in self.variables if x.name == isim), None)
            if v is None:
                raise ValueError("izlenmeyen değişkene yazılamaz: %s" % isim)
            if not v.addr:
                raise RuntimeError("adresi çözülmemiş: %s" % isim)
            if self._rsp is None:
                raise RuntimeError("bağlantı yok")

            bicim = TYPES[v.type][0]
            try:
                sayi = float(deger) if v.type in ("f32", "f64") else int(deger)
                ham = struct.pack(bicim, sayi)
            except (TypeError, ValueError, struct.error) as exc:
                raise ValueError(
                    "%s (%s) için geçersiz değer: %r — %s"
                    % (isim, v.type, deger, exc)) from exc

            self._rsp.write_mem(v.addr, ham)
            return sayi

    def stream(self, limit: int = 0, retry: bool = True,
               on_event=None) -> Iterator[Sample]:
        period = 1.0 / self.hz if self.hz > 0 else 0.0
        t0 = time.perf_counter()
        next_t = t0
        seq = 0

        kalan_deneme = RETRY_MAX

        son_kontrol = time.perf_counter()

        while limit == 0 or seq < limit:
            try:
                if time.perf_counter() - son_kontrol >= WATCHDOG_S:
                    son_kontrol = time.perf_counter()
                    if not self.hat_canli():
                        raise ProbeError(
                            "kart yanit vermiyor - ST-LINK cikarilmis olabilir")
                values = self.read_once()
            except (OSError, RuntimeError) as exc:
                if not retry:
                    raise
                if on_event:
                    on_event("koptu", str(exc))
                kalan_deneme -= self._yeniden_baglan(kalan_deneme, on_event)
                next_t = time.perf_counter()
                continue

            kalan_deneme = RETRY_MAX
            yield Sample(seq=seq, t=time.perf_counter() - t0, values=values)
            seq += 1

            if period:
                next_t += period
                gap = next_t - time.perf_counter()
                if gap > 0:
                    time.sleep(gap)
                else:
                    next_t = time.perf_counter()

    def meta(self) -> dict:
        return {
            "type": "header",
            "format": FORMAT_VERSION,
            "started": datetime.now().isoformat(timespec="seconds"),
            "hz": self.hz,
            "elf": self.elf,
            "vars": [
                {"name": v.name, "type": v.type, "addr": "0x%08X" % v.addr}
                for v in self.variables
            ],
        }


class Sink:
    def header(self, meta: dict) -> None: ...

    def sample(self, s: Sample) -> None: ...

    def end(self, meta: dict) -> None: ...

    def event(self, obj: dict) -> None:
        pass

    def close(self) -> None: ...


class NdjsonSink(Sink):
    def __init__(self, stream: TextIO) -> None:
        self._out = stream

    def _write(self, obj: dict) -> None:
        self._out.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
        self._out.write("\n")
        self._out.flush()

    def header(self, meta: dict) -> None:
        self._write(meta)

    def sample(self, s: Sample) -> None:
        self._write(
            {"type": "sample", "seq": s.seq, "t": round(s.t, 6), "values": s.values}
        )

    def end(self, meta: dict) -> None:
        self._write(meta)

    def event(self, obj: dict) -> None:
        self._write(obj)


class CsvSink(Sink):
    def __init__(self, stream: TextIO) -> None:
        self._out = stream
        self._names: list[str] = []

    def header(self, meta: dict) -> None:
        self._names = [v["name"] for v in meta["vars"]]
        self._out.write("t,seq," + ",".join(self._names) + "\n")
        self._out.flush()

    def sample(self, s: Sample) -> None:
        cells = [("%.6f" % s.t), str(s.seq)]
        cells += [str(s.values.get(n, "")) for n in self._names]
        self._out.write(",".join(cells) + "\n")

    def end(self, meta: dict) -> None:
        self._out.flush()


class UdpSink(Sink):
    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        import socket

        self._addr = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        if len(data) <= 65507:
            self._sock.sendto(data, self._addr)

    def header(self, meta: dict) -> None:
        self._send(meta)

    def sample(self, s: Sample) -> None:
        self._send({"type": "sample", "seq": s.seq, "t": round(s.t, 6), "values": s.values})

    def end(self, meta: dict) -> None:
        self._send(meta)

    def event(self, obj: dict) -> None:
        self._send(obj)

    def close(self) -> None:
        self._sock.close()


class FanOut(Sink):
    def __init__(self, sinks: list[Sink]) -> None:
        self._sinks = sinks

    def header(self, meta: dict) -> None:
        for s in self._sinks:
            s.header(meta)

    def sample(self, sample: Sample) -> None:
        for s in self._sinks:
            s.sample(sample)

    def end(self, meta: dict) -> None:
        for s in self._sinks:
            s.end(meta)

    def event(self, obj: dict) -> None:
        for s in self._sinks:
            s.event(obj)

    def close(self) -> None:
        for s in self._sinks:
            s.close()


def timestamped_path(out_dir: str, suffix: str) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / (datetime.now().strftime("%Y%m%d-%H%M%S") + suffix)


_IZINLI_DUGUMLER: Final[tuple] = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare,
    ast.Name, ast.Load, ast.Constant, ast.Call,
    ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

_IZINLI_FONKSIYONLAR: Final[dict] = {
    "abs": abs, "min": min, "max": max, "round": round,
}


class ExpressionError(ValueError):
    pass


class Expression:
    def __init__(self, kaynak: str, degisken_adlari) -> None:
        self.kaynak = kaynak.strip()
        if not self.kaynak:
            raise ExpressionError("ifade bos")
        try:
            agac = ast.parse(self.kaynak, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError("sozdizimi hatasi: %s" % exc.msg) from exc

        bilinen = set(degisken_adlari)
        for dugum in ast.walk(agac):
            if not isinstance(dugum, _IZINLI_DUGUMLER):
                raise ExpressionError(
                    "ifadede kullanilamaz: %s" % type(dugum).__name__)
            if isinstance(dugum, ast.Call):
                cagrilabilir = (isinstance(dugum.func, ast.Name)
                                and dugum.func.id in _IZINLI_FONKSIYONLAR)
                if not cagrilabilir:
                    raise ExpressionError(
                        "yalnizca su fonksiyonlar kullanilabilir: %s"
                        % ", ".join(sorted(_IZINLI_FONKSIYONLAR)))
            if isinstance(dugum, ast.Name) and (
                    dugum.id not in bilinen
                    and dugum.id not in _IZINLI_FONKSIYONLAR):
                raise ExpressionError(
                    "bilinmeyen ad: %s (secili degiskenler: %s)"
                    % (dugum.id, ", ".join(sorted(bilinen)) or "yok"))

        kullanilan = {d.id for d in ast.walk(agac)
                      if isinstance(d, ast.Name) and d.id in bilinen}
        if not kullanilan:
            raise ExpressionError(
                "kosul en az bir degisken kullanmali. Ornek: %s > 100"
                % (sorted(bilinen)[0] if bilinen else "degisken"))

        govde = agac.body
        karsilastirma = isinstance(govde, (ast.Compare, ast.BoolOp)) or (
            isinstance(govde, ast.UnaryOp) and isinstance(govde.op, ast.Not))
        if not karsilastirma:
            raise ExpressionError(
                "kosul bir karsilastirma olmali (>, <, ==, and, or). "
                "Ornek: %s > 100" % sorted(kullanilan)[0])

        self._kod = compile(agac, "<tetikleyici>", "eval")

    def __call__(self, values: dict) -> bool:
        ortam = dict(_IZINLI_FONKSIYONLAR)
        ortam.update(values)
        return bool(eval(self._kod, {"__builtins__": {}}, ortam))

    def __repr__(self) -> str:
        return "Expression(%r)" % self.kaynak


class Trigger:
    BEKLIYOR: Final[str] = "bekliyor"
    YAKALIYOR: Final[str] = "yakaliyor"
    BITTI: Final[str] = "bitti"

    def __init__(self, ifade: Expression, pre: int = 200, post: int = 200,
                 rearm: bool = True, max_captures: int = 0) -> None:
        self.ifade = ifade
        self.pre = max(0, pre)
        self.post = max(1, post)
        self.rearm = rearm
        self.max_captures = max_captures
        self.yakalama = 0
        self.durum = self.BEKLIYOR
        self._tampon = deque(maxlen=self.pre) if self.pre else deque(maxlen=0)
        self._kalan = 0
        self._onceki = None

    def feed(self, sample: Sample):
        if self.durum == self.BITTI:
            return (), None

        try:
            kosul = self.ifade(sample.values)
        except (TypeError, ValueError, ZeroDivisionError, NameError):
            kosul = False

        if self.durum == self.YAKALIYOR:
            self._onceki = kosul
            self._kalan -= 1
            if self._kalan > 0:
                return (sample,), None
            self.yakalama += 1
            if self.rearm and (self.max_captures == 0
                               or self.yakalama < self.max_captures):
                self.durum = self.BEKLIYOR
            else:
                self.durum = self.BITTI
            return (sample,), "tamamlandi"

        ilk_ornek = self._onceki is None
        yukselen_kenar = kosul and not self._onceki and not ilk_ornek
        self._onceki = kosul
        if not yukselen_kenar:
            self._tampon.append(sample)
            return (), None

        self.durum = self.YAKALIYOR
        self._kalan = self.post
        cikti = tuple(self._tampon) + (sample,)
        self._tampon.clear()
        return cikti, "tetiklendi"

def ndjson_to_csv(nd_yol, csv_yol) -> int:
    adlar: list[str] = []
    gorulen: set[str] = set()

    def ornekler(dosya):
        for satir in dosya:
            if '"sample"' not in satir:
                continue
            try:
                m = json.loads(satir)
            except ValueError:
                continue
            if m.get("type") == "sample":
                yield m

    with open(nd_yol, encoding="utf-8") as f:
        for m in ornekler(f):
            for ad in m["values"]:
                if ad not in gorulen:
                    gorulen.add(ad)
                    adlar.append(ad)
    if not adlar:
        return 0

    n = 0
    with (open(nd_yol, encoding="utf-8") as f,
          open(csv_yol, "w", encoding="utf-8", newline="\n") as c):
        c.write("t,seq," + ",".join(adlar) + "\n")
        for m in ornekler(f):
            hucre = ["%.6f" % m["t"], str(m["seq"])]
            hucre += [str(m["values"].get(ad, "")) for ad in adlar]
            c.write(",".join(hucre) + "\n")
            n += 1
    return n
