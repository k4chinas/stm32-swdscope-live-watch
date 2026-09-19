from __future__ import annotations

import struct
from typing import Final


TAG_ARRAY: Final = 0x01
TAG_ENUM: Final = 0x04
TAG_MEMBER: Final = 0x0D
TAG_POINTER: Final = 0x0F
TAG_STRUCT: Final = 0x13
TAG_TYPEDEF: Final = 0x16
TAG_UNION: Final = 0x17
TAG_BASE: Final = 0x24
TAG_CONST: Final = 0x26
TAG_SUBRANGE: Final = 0x21
TAG_VOLATILE: Final = 0x35
TAG_VARIABLE: Final = 0x34
TAG_RESTRICT: Final = 0x37

AT_LOCATION: Final = 0x02
AT_NAME: Final = 0x03
AT_BYTE_SIZE: Final = 0x0B
AT_UPPER_BOUND: Final = 0x2F
AT_COUNT: Final = 0x37
AT_DATA_MEMBER_LOCATION: Final = 0x38
AT_DECLARATION: Final = 0x3C
AT_ENCODING: Final = 0x3E
AT_TYPE: Final = 0x49
AT_STR_OFFSETS_BASE: Final = 0x72

ATE_BOOLEAN: Final = 0x02
ATE_FLOAT: Final = 0x04
ATE_SIGNED: Final = 0x05
ATE_SIGNED_CHAR: Final = 0x06
ATE_UNSIGNED: Final = 0x07
ATE_UNSIGNED_CHAR: Final = 0x08

DW_OP_ADDR: Final = 0x03

EN_DERIN: Final = 3
DIZI_SINIRI: Final = 16


class DwarfHatasi(Exception):
    pass


def _bolumler(yol: str) -> dict[str, bytes]:
    with open(yol, "rb") as fh:
        veri = fh.read()

    if veri[:4] != bytes([0x7F]) + b"ELF" or veri[4] != 1:
        raise DwarfHatasi("32 bit ELF değil")

    e_shoff, = struct.unpack_from("<I", veri, 0x20)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", veri, 0x2E)

    ham = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        ad_off, _tip, _bayrak, _adres, s_off, boyut = struct.unpack_from(
            "<IIIIII", veri, off)
        ham.append((ad_off, s_off, boyut))

    _n, str_off, _b = ham[e_shstrndx]
    NUL = bytes(1)
    out = {}
    for ad_off, s_off, boyut in ham:
        son = veri.index(NUL, str_off + ad_off)
        ad = veri[str_off + ad_off:son].decode("utf-8", "replace")
        out[ad] = veri[s_off:s_off + boyut]
    return out


class _Akis:
    __slots__ = ("veri", "i")

    def __init__(self, veri: bytes, i: int = 0) -> None:
        self.veri = veri
        self.i = i

    def bitti(self) -> bool:
        return self.i >= len(self.veri)

    def u8(self) -> int:
        d = self.veri[self.i]
        self.i += 1
        return d

    def u16(self) -> int:
        d, = struct.unpack_from("<H", self.veri, self.i)
        self.i += 2
        return d

    def u32(self) -> int:
        d, = struct.unpack_from("<I", self.veri, self.i)
        self.i += 4
        return d

    def u64(self) -> int:
        d, = struct.unpack_from("<Q", self.veri, self.i)
        self.i += 8
        return d

    def uleb(self) -> int:
        sonuc = kaydir = 0
        while True:
            b = self.u8()
            sonuc |= (b & 0x7F) << kaydir
            if not b & 0x80:
                return sonuc
            kaydir += 7

    def sleb(self) -> int:
        sonuc = kaydir = 0
        while True:
            b = self.u8()
            sonuc |= (b & 0x7F) << kaydir
            kaydir += 7
            if not b & 0x80:
                if b & 0x40:
                    sonuc -= 1 << kaydir
                return sonuc

    def baytlar(self, n: int) -> bytes:
        d = self.veri[self.i:self.i + n]
        self.i += n
        return d

    def dize(self) -> str:
        son = self.veri.index(bytes(1), self.i)
        d = self.veri[self.i:son].decode("utf-8", "replace")
        self.i = son + 1
        return d


def _str_al(bolum: bytes, ofset: int) -> str:
    if not bolum or ofset >= len(bolum):
        return ""
    son = bolum.index(bytes(1), ofset)
    return bolum[ofset:son].decode("utf-8", "replace")


def _kisaltmalar(veri: bytes, ofset: int) -> dict:
    a = _Akis(veri, ofset)
    tablo = {}
    while not a.bitti():
        kod = a.uleb()
        if kod == 0:
            break
        etiket = a.uleb()
        cocuk = a.u8() != 0
        alanlar = []
        while True:
            attr = a.uleb()
            form = a.uleb()
            sabit = a.sleb() if form == 0x21 else None
            if attr == 0 and form == 0:
                break
            alanlar.append((attr, form, sabit))
        tablo[kod] = (etiket, cocuk, alanlar)
    return tablo


def _deger(a: _Akis, form: int, sabit, cu) -> object:
    adres_boyu = cu["adres_boyu"]
    ofs_boyu = cu["ofset_boyu"]

    if form == 0x01:
        return a.u32() if adres_boyu == 4 else a.u64()
    if form in (0x0B, 0x0C):
        return a.u8()
    if form == 0x1C:
        return a.u32()
    if form == 0x1D:
        return a.u32() if ofs_boyu == 4 else a.u64()
    if form == 0x1E:
        return a.baytlar(16)
    if form == 0x05:
        return a.u16()
    if form == 0x06:
        return a.u32()
    if form == 0x07:
        return a.u64()
    if form == 0x24:
        return a.u64()
    if form == 0x0D:
        return a.sleb()
    if form == 0x0F:
        return a.uleb()
    if form == 0x08:
        return a.dize()
    if form == 0x0E:
        ofs = a.u32() if ofs_boyu == 4 else a.u64()
        return _str_al(cu["str"], ofs)
    if form == 0x1F:
        ofs = a.u32() if ofs_boyu == 4 else a.u64()
        return _str_al(cu["line_str"], ofs)
    if form in (0x1A, 0x25, 0x26, 0x27, 0x28):
        if form == 0x1A:
            dizin = a.uleb()
        else:
            dizin = int.from_bytes(a.baytlar(form - 0x24), "little")
        return _strx(cu, dizin)
    if form in (0x1B, 0x29, 0x2A, 0x2B, 0x2C):
        if form == 0x1B:
            a.uleb()
        else:
            a.baytlar(form - 0x28)
        return 0
    if form == 0x11:
        return cu["bas"] + a.u8()
    if form == 0x12:
        return cu["bas"] + a.u16()
    if form == 0x13:
        return cu["bas"] + a.u32()
    if form == 0x14:
        return cu["bas"] + a.u64()
    if form == 0x15:
        return cu["bas"] + a.uleb()
    if form == 0x10:
        boy = adres_boyu if cu["surum"] == 2 else ofs_boyu
        return a.u32() if boy == 4 else a.u64()
    if form == 0x17:
        return a.u32() if ofs_boyu == 4 else a.u64()
    if form == 0x18:
        return a.baytlar(a.uleb())
    if form == 0x09:
        return a.baytlar(a.uleb())
    if form == 0x0A:
        return a.baytlar(a.u8())
    if form == 0x03:
        return a.baytlar(a.u16())
    if form == 0x04:
        return a.baytlar(a.u32())
    if form == 0x19:
        return 1
    if form == 0x21:
        return sabit
    if form == 0x20:
        return a.u64()
    if form in (0x22, 0x23):
        return a.uleb()
    if form == 0x16:
        return _deger(a, a.uleb(), None, cu)
    raise DwarfHatasi("bilinmeyen form: 0x%02X" % form)


def _strx(cu, dizin: int) -> str:
    tablo = cu["str_offsets"]
    taban = cu["str_off_base"]
    yer = taban + dizin * 4
    if not tablo or yer + 4 > len(tablo):
        return ""
    ofs, = struct.unpack_from("<I", tablo, yer)
    return _str_al(cu["str"], ofs)


def _cu_oku(a: _Akis, bolumler: dict) -> tuple[dict, dict]:
    bas = a.i
    uzunluk = a.u32()
    if uzunluk == 0xFFFFFFFF:
        raise DwarfHatasi("64 bit DWARF desteklenmiyor")
    son = a.i + uzunluk
    surum = a.u16()

    if surum >= 5:
        _unit_tipi = a.u8()
        adres_boyu = a.u8()
        abbrev_ofs = a.u32()
    else:
        abbrev_ofs = a.u32()
        adres_boyu = a.u8()

    cu = {
        "bas": bas,
        "surum": surum,
        "adres_boyu": adres_boyu,
        "ofset_boyu": 4,
        "str": bolumler.get(".debug_str", b""),
        "line_str": bolumler.get(".debug_line_str", b""),
        "str_offsets": bolumler.get(".debug_str_offsets", b""),
        "str_off_base": 8,
    }
    kisalt = _kisaltmalar(bolumler.get(".debug_abbrev", b""), abbrev_ofs)

    dieler: dict[int, dict] = {}
    yigin: list[dict] = []
    kok = None

    while a.i < son:
        ofset = a.i
        kod = a.uleb()
        if kod == 0:
            if yigin:
                yigin.pop()
            continue
        if kod not in kisalt:
            raise DwarfHatasi("kısaltma kodu yok: %d" % kod)

        etiket, cocuk_var, alanlar = kisalt[kod]
        attrs = {}
        for attr, form, sabit in alanlar:
            d = _deger(a, form, sabit, cu)
            if attr:
                attrs[attr] = d
            if attr == AT_STR_OFFSETS_BASE:
                cu["str_off_base"] = d

        die = {"etiket": etiket, "attrs": attrs, "cocuklar": [], "ofset": ofset}
        dieler[ofset] = die
        if yigin:
            yigin[-1]["cocuklar"].append(die)
        elif kok is None:
            kok = die
        if cocuk_var:
            yigin.append(die)

    a.i = son
    return cu, dieler


_TIPLER = {
    (ATE_FLOAT, 4): "f32", (ATE_FLOAT, 8): "f64",
    (ATE_SIGNED, 1): "i8", (ATE_SIGNED, 2): "i16",
    (ATE_SIGNED, 4): "i32", (ATE_SIGNED, 8): "i64",
    (ATE_SIGNED_CHAR, 1): "i8",
    (ATE_UNSIGNED, 1): "u8", (ATE_UNSIGNED, 2): "u16",
    (ATE_UNSIGNED, 4): "u32", (ATE_UNSIGNED, 8): "u64",
    (ATE_UNSIGNED_CHAR, 1): "u8",
    (ATE_BOOLEAN, 1): "u8",
}


def _sadelestir(die, dieler, derinlik=0):
    while die is not None and derinlik < 16:
        if die["etiket"] in (TAG_TYPEDEF, TAG_CONST, TAG_VOLATILE, TAG_RESTRICT):
            ref = die["attrs"].get(AT_TYPE)
            die = dieler.get(ref) if isinstance(ref, int) else None
            derinlik += 1
            continue
        return die
    return die


def _tip_adi(die, dieler) -> str | None:
    die = _sadelestir(die, dieler)
    if die is None:
        return None
    if die["etiket"] == TAG_POINTER:
        return "u32"
    if die["etiket"] == TAG_ENUM:
        boyut = die["attrs"].get(AT_BYTE_SIZE, 4)
        return _TIPLER.get((ATE_UNSIGNED, boyut))
    if die["etiket"] != TAG_BASE:
        return None
    return _TIPLER.get((die["attrs"].get(AT_ENCODING),
                        die["attrs"].get(AT_BYTE_SIZE)))


def _boyut(die, dieler) -> int:
    die = _sadelestir(die, dieler)
    if die is None:
        return 0
    return die["attrs"].get(AT_BYTE_SIZE, 0) or 0


def _dizi_bilgisi(die, dieler):
    eleman = dieler.get(die["attrs"].get(AT_TYPE))
    sayi = 0
    for c in die["cocuklar"]:
        if c["etiket"] != TAG_SUBRANGE:
            continue
        if AT_COUNT in c["attrs"]:
            sayi = c["attrs"][AT_COUNT]
        elif AT_UPPER_BOUND in c["attrs"]:
            ust = c["attrs"][AT_UPPER_BOUND]
            sayi = ust + 1 if isinstance(ust, int) else 0
    return eleman, sayi if isinstance(sayi, int) else 0


def _ac(isim, tip_die, adres, dieler, sonuc, derinlik=0):
    tip_die = _sadelestir(tip_die, dieler)
    if tip_die is None or derinlik > EN_DERIN:
        return

    ad = _tip_adi(tip_die, dieler)
    if ad:
        sonuc[isim] = (adres, ad)
        return

    if tip_die["etiket"] in (TAG_STRUCT, TAG_UNION):
        for uye in tip_die["cocuklar"]:
            if uye["etiket"] != TAG_MEMBER:
                continue
            uye_ad = uye["attrs"].get(AT_NAME)
            if not isinstance(uye_ad, str):
                continue
            kaydirma = uye["attrs"].get(AT_DATA_MEMBER_LOCATION, 0)
            if isinstance(kaydirma, bytes):
                kaydirma = kaydirma[1] if len(kaydirma) > 1 else 0
            if not isinstance(kaydirma, int):
                continue
            _ac("%s.%s" % (isim, uye_ad), dieler.get(uye["attrs"].get(AT_TYPE)),
                adres + kaydirma, dieler, sonuc, derinlik + 1)
        return

    if tip_die["etiket"] == TAG_ARRAY:
        eleman, sayi = _dizi_bilgisi(tip_die, dieler)
        eleman_boyu = _boyut(eleman, dieler)
        if not eleman or not eleman_boyu or not 0 < sayi <= DIZI_SINIRI:
            return
        for i in range(sayi):
            _ac("%s[%d]" % (isim, i), eleman, adres + i * eleman_boyu,
                dieler, sonuc, derinlik + 1)


def degisken_tipleri(elf_yolu: str) -> dict[str, tuple[int, str]]:
    try:
        bolumler = _bolumler(elf_yolu)
    except (OSError, ValueError, IndexError, struct.error) as exc:
        raise DwarfHatasi("ELF okunamadı: %s" % exc) from exc

    info = bolumler.get(".debug_info")
    if not info or not bolumler.get(".debug_abbrev"):
        raise DwarfHatasi("hata ayıklama bilgisi yok (-g ile derlenmiş mi?)")

    sonuc: dict[str, tuple[int, str]] = {}
    a = _Akis(info)
    while len(info) - a.i >= 11:
        uzunluk, = struct.unpack_from("<I", info, a.i)
        birim_sonu = a.i + 4 + uzunluk
        try:
            _cu, dieler = _cu_oku(a, bolumler)
        except (DwarfHatasi, struct.error, IndexError, ValueError, KeyError):
            if not (0 < uzunluk and birim_sonu <= len(info)):
                break
            a.i = birim_sonu
            continue
        a.i = max(a.i, min(birim_sonu, len(info)))

        for die in dieler.values():
            if die["etiket"] != TAG_VARIABLE:
                continue
            if die["attrs"].get(AT_DECLARATION):
                continue
            isim = die["attrs"].get(AT_NAME)
            yer = die["attrs"].get(AT_LOCATION)
            if not isinstance(isim, str) or not isinstance(yer, bytes):
                continue
            if len(yer) < 5 or yer[0] != DW_OP_ADDR:
                continue
            adres, = struct.unpack_from("<I", yer, 1)
            if not adres:
                continue
            _ac(isim, dieler.get(die["attrs"].get(AT_TYPE)), adres,
                dieler, sonuc)

    return sonuc
