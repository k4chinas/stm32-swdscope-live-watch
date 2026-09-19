from __future__ import annotations

import io
import json
import struct

import pytest

import swdscope_core as core
from swdscope_core import (
    ElfHatasi,
    Expression,
    elf_gecmisine_ekle,
    ndjson_to_csv,
    ExpressionError,
    ProbeError,
    Trigger,
    CsvSink,
    FanOut,
    NdjsonSink,
    Profile,
    Sample,
    Sampler,
    VarSpec,
    default_type_for,
)

BASE = 0x20000100


class FakeRsp:
    def __init__(self, image: bytes, base: int = BASE) -> None:
        self.image = image
        self.base = base
        self.reads: list[tuple[int, int]] = []
        self.closed = False
        self.hat_canli = True

    CPUID = 0x410FC241

    def read_mem(self, addr: int, length: int) -> bytes:
        if addr == core.CPUID_ADRES:
            if not self.hat_canli:
                raise RuntimeError("hat kopuk")
            return struct.pack("<I", self.CPUID)
        self.reads.append((addr, length))
        off = addr - self.base
        if off < 0 or off + length > len(self.image):
            raise RuntimeError("aralık dışı okuma: 0x%08X" % addr)
        return self.image[off : off + length]

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def bellek():
    return struct.pack("<IfhH", 1883, 0.5, -300, 0)


def sampler_kur(monkeypatch, rsp, variables, hz=50.0) -> Sampler:
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (rsp, None))
    s = Sampler(variables, elf="", hz=hz)
    s.open()
    return s


def test_tek_blok_okuma(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    s = sampler_kur(monkeypatch, rsp, [
        VarSpec("sayac", "u32", BASE),
        VarSpec("sinus", "f32", BASE + 4),
    ])
    degerler = s.read_once()

    assert degerler["sayac"] == 1883
    assert degerler["sinus"] == pytest.approx(0.5)
    assert len(rsp.reads) == 1, "iki değişken tek blokta okunmalıydı"


def test_uzak_adresler_ayri_okunur(monkeypatch):
    uzak = BASE + core.MAX_BLOCK + 64
    image = bytearray(core.MAX_BLOCK + 128)
    struct.pack_into("<I", image, 0, 7)
    struct.pack_into("<I", image, uzak - BASE, 9)

    rsp = FakeRsp(bytes(image))
    s = sampler_kur(monkeypatch, rsp, [
        VarSpec("yakin", "u32", BASE),
        VarSpec("uzak", "u32", uzak),
    ])
    degerler = s.read_once()

    assert degerler == {"yakin": 7, "uzak": 9}
    assert len(rsp.reads) == 2


def test_tip_cozumleme(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    s = sampler_kur(monkeypatch, rsp, [
        VarSpec("hiz", "i16", BASE + 8),
        VarSpec("ham", "u32", BASE),
    ])
    degerler = s.read_once()
    assert degerler["hiz"] == -300, "işaretli tip negatif okunmalı"
    assert degerler["ham"] == 1883


def test_akis_sira_ve_zaman(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    s = sampler_kur(monkeypatch, rsp, [VarSpec("sayac", "u32", BASE)], hz=1000.0)
    ornekler = list(s.stream(limit=5))

    assert [o.seq for o in ornekler] == [0, 1, 2, 3, 4]
    assert all(o.t >= 0 for o in ornekler)
    assert ornekler[-1].t >= ornekler[0].t


def test_adressiz_degisken_hata(monkeypatch):
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (FakeRsp(b""), None))
    s = Sampler([VarSpec("bilinmeyen", "u32", 0)], elf="", hz=10.0)
    with pytest.raises(RuntimeError, match="adresi bilinmeyen"):
        s.open()


def test_bos_degisken_listesi():
    with pytest.raises(ValueError, match="değişken seçilmedi"):
        Sampler([], elf="", hz=10.0)


def test_meta_basligi(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    s = sampler_kur(monkeypatch, rsp, [VarSpec("sayac", "u32", BASE)])
    meta = s.meta()
    assert meta["type"] == "header"
    assert meta["format"] == core.FORMAT_VERSION
    assert meta["vars"][0] == {
        "name": "sayac", "type": "u32", "addr": "0x%08X" % BASE
    }


def test_kapatma(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    s = sampler_kur(monkeypatch, rsp, [VarSpec("sayac", "u32", BASE)])
    s.close()
    assert rsp.closed


def test_profil_gidis_donus(tmp_path):
    yol = tmp_path / "profil.json"
    p = Profile(elf="C:/proje/Debug/a.elf", hz=100.0)
    p.variables = [VarSpec("g_tick", "u32", 0x20000010),
                   VarSpec("g_sine", "f32", 0x20000014)]
    p.save(yol)

    geri = Profile.load(yol)
    assert geri.elf == p.elf
    assert geri.hz == 100.0
    assert [(v.name, v.type, v.addr) for v in geri.variables] == [
        ("g_tick", "u32", 0x20000010),
        ("g_sine", "f32", 0x20000014),
    ], "elle girilen adresler de korunmalı"


def test_profil_yoksa_varsayilan(tmp_path):
    p = Profile.load(tmp_path / "yok.json")
    assert p.elf == "" and p.variables == []


def test_bozuk_profil_cokmesin(tmp_path):
    yol = tmp_path / "bozuk.json"
    yol.write_text("{ bu json degil", encoding="utf-8")
    assert Profile.load(yol).variables == []


def test_bilinmeyen_tip_atlanir(tmp_path):
    yol = tmp_path / "p.json"
    yol.write_text(json.dumps({"variables": [
        {"name": "iyi", "type": "u32"},
        {"name": "kotu", "type": "float128"},
    ]}), encoding="utf-8")
    adlar = [v.name for v in Profile.load(yol).variables]
    assert adlar == ["iyi"]


def test_varsayilan_tipler():
    assert default_type_for(1) == "u8"
    assert default_type_for(2) == "u16"
    assert default_type_for(4) == "u32"
    assert default_type_for(99) == "u32"


def _meta():
    return {"type": "header", "format": 1, "hz": 50.0,
            "vars": [{"name": "a", "type": "u32", "addr": "0x20000000"},
                     {"name": "b", "type": "f32", "addr": "0x20000004"}]}


def test_ndjson_satir_satir_okunabilir():
    buf = io.StringIO()
    sink = NdjsonSink(buf)
    sink.header(_meta())
    sink.sample(Sample(seq=0, t=0.0, values={"a": 1, "b": 0.5}))
    sink.sample(Sample(seq=1, t=0.02, values={"a": 2, "b": 0.6}))
    sink.end({"type": "end", "samples": 2})

    satirlar = buf.getvalue().strip().split("\n")
    assert len(satirlar) == 4
    mesajlar = [json.loads(s) for s in satirlar]
    assert mesajlar[0]["type"] == "header"
    assert mesajlar[1]["values"] == {"a": 1, "b": 0.5}
    assert mesajlar[2]["seq"] == 1
    assert mesajlar[3]["type"] == "end"


def test_csv_sutunlari():
    buf = io.StringIO()
    sink = CsvSink(buf)
    sink.header(_meta())
    sink.sample(Sample(seq=0, t=0.5, values={"a": 1, "b": 0.5}))
    sink.end({})

    satirlar = buf.getvalue().strip().split("\n")
    assert satirlar[0] == "t,seq,a,b"
    assert satirlar[1] == "0.500000,0,1,0.5"


def test_fanout_hepsine_yazar():
    a, b = io.StringIO(), io.StringIO()
    sink = FanOut([NdjsonSink(a), NdjsonSink(b)])
    sink.header(_meta())
    sink.sample(Sample(seq=0, t=0.0, values={"a": 1}))
    assert a.getvalue() == b.getvalue()
    assert a.getvalue().count("\n") == 2


def test_kart_yoksa_anlasilir_hata(monkeypatch):
    def patlat(*a, **k):
        raise OSError("baglanti reddedildi")

    monkeypatch.setattr(core, "open_link", patlat)
    monkeypatch.setattr(core, "detect_probe", lambda: None)

    s = Sampler([VarSpec("a", "u32", BASE)], elf="", hz=10.0)
    with pytest.raises(ProbeError, match="Kart bulunamadi"):
        s.open()


def test_probe_mesgulse_farkli_mesaj(monkeypatch):
    def patlat(*a, **k):
        raise OSError("baglanti reddedildi")

    monkeypatch.setattr(core, "open_link", patlat)
    monkeypatch.setattr(core, "detect_probe",
                        lambda: core.ProbeInfo(serial="ABC123"))

    s = Sampler([VarSpec("a", "u32", BASE)], elf="", hz=10.0)
    with pytest.raises(ProbeError, match="tek istemci"):
        s.open()


def test_kopmada_yeniden_baglanir(monkeypatch, bellek):
    monkeypatch.setattr(core, "RETRY_INTERVAL_S", 0.0)

    class KopanRsp(FakeRsp):
        def __init__(self, image, kopma_no):
            super().__init__(image)
            self.kopma_no = kopma_no
            self.sayac = 0

        def read_mem(self, addr, length):
            self.sayac += 1
            if self.sayac == self.kopma_no:
                raise OSError("baglanti koptu")
            return super().read_mem(addr, length)

    kopan = KopanRsp(bellek, kopma_no=3)
    saglam = FakeRsp(bellek)
    sirasi = [kopan, saglam]
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (sirasi.pop(0), None))

    olaylar = []
    s = Sampler([VarSpec("sayac", "u32", BASE)], elf="", hz=1000.0)
    s.open()
    ornekler = list(s.stream(limit=6, on_event=lambda t, d: olaylar.append(t)))

    assert [o.seq for o in ornekler] == [0, 1, 2, 3, 4, 5], "seq atlamamalı"
    assert olaylar == ["koptu", "baglandi"]
    assert kopan.closed, "kopan bağlantı kapatılmalı"


def test_retry_kapaliysa_hata_disari_cikar(monkeypatch, bellek):
    class HepPatlayan(FakeRsp):
        def read_mem(self, addr, length):
            if addr == core.CPUID_ADRES:
                return super().read_mem(addr, length)
            raise OSError("kopuk")

    monkeypatch.setattr(core, "open_link",
                        lambda *a, **k: (HepPatlayan(bellek), None))
    s = Sampler([VarSpec("a", "u32", BASE)], elf="", hz=100.0)
    s.open()
    with pytest.raises(OSError, match="kopuk"):
        list(s.stream(limit=3, retry=False))


def test_olay_satiri_kayda_yazilir():
    buf = io.StringIO()
    sink = NdjsonSink(buf)
    sink.event({"type": "link", "event": "koptu", "detail": "x", "t": 1.5})
    mesaj = json.loads(buf.getvalue().strip())
    assert mesaj["type"] == "link" and mesaj["event"] == "koptu"


def test_baglanamayinca_vazgecer(monkeypatch, bellek):
    monkeypatch.setattr(core, "RETRY_INTERVAL_S", 0.0)

    class KopanRsp(FakeRsp):
        def read_mem(self, addr, length):
            if addr == core.CPUID_ADRES:
                return super().read_mem(addr, length)
            raise OSError("kopuk")

    ilk = KopanRsp(bellek)
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (ilk, None))

    s = Sampler([VarSpec("a", "u32", BASE)], elf="", hz=1000.0)
    s.open()

    olaylar = []
    with pytest.raises(ProbeError, match="vazgecildi"):
        list(s.stream(limit=5, on_event=lambda t, d: olaylar.append(t)))

    assert olaylar[0] == "koptu"
    assert olaylar[-1] == "vazgecildi"


def test_deneme_sayisi_sinirli(monkeypatch, bellek):
    monkeypatch.setattr(core, "RETRY_INTERVAL_S", 0.0)
    monkeypatch.setattr(core, "RETRY_MAX", 4)

    sayac = {"n": 0}

    def acilamaz(*a, **k):
        sayac["n"] += 1
        raise OSError("acilamiyor")

    s = Sampler([VarSpec("a", "u32", BASE)], elf="", hz=1000.0)
    monkeypatch.setattr(core, "open_link", acilamaz)
    monkeypatch.setattr(core, "detect_probe", lambda: None)

    with pytest.raises(ProbeError):
        s._yeniden_baglan(4, None)
    assert sayac["n"] == 4, "tam RETRY_MAX kez denenmeli"


ADLAR = ["hata", "hiz", "fark"]
DEGERLER = {"hata": 7, "hiz": 2, "fark": -0.9}


@pytest.mark.parametrize("kaynak, beklenen", [
    ("hata > 5", True),
    ("hata > 50", False),
    ("abs(fark) > 0.5 and hiz < 10", True),
    ("hata > 5 or not (hiz > 3)", True),
    ("hata - hiz == 5", True),
    ("max(hata, hiz) > 6", True),
])


def test_ifade_dogru_hesaplanir(kaynak, beklenen):
    assert Expression(kaynak, ADLAR)(DEGERLER) is beklenen


@pytest.mark.parametrize("kaynak, mesaj", [
    ("hata.__class__", "kullanilamaz"),
    ("open('x')", "fonksiyonlar"),
    ("[1, 2][0] > 1", "kullanilamaz"),
    ("yok > 1", "bilinmeyen ad"),
    ("hata >", "sozdizimi"),
    ("", "bos"),
])


def test_tehlikeli_ifade_reddedilir(kaynak, mesaj):
    with pytest.raises(ExpressionError, match=mesaj):
        Expression(kaynak, ADLAR)


def _ornek(seq, deger):
    return Sample(seq=seq, t=seq * 0.01, values={"hata": deger})


def test_tetik_oncesini_saklar():
    t = Trigger(Expression("hata > 5", ["hata"]), pre=3, post=2)

    for i in range(6):
        assert t.feed(_ornek(i, 0))[0] == ()

    yazilacak, olay = t.feed(_ornek(6, 9))
    assert olay == "tetiklendi"
    assert [o.seq for o in yazilacak] == [3, 4, 5, 6]


def test_tetik_sonrasini_yazar_ve_biter():
    t = Trigger(Expression("hata > 5", ["hata"]), pre=2, post=3,
                rearm=False)
    t.feed(_ornek(0, 0))
    t.feed(_ornek(1, 9))

    for i in (2, 3):
        yazilacak, olay = t.feed(_ornek(i, 9))
        assert [o.seq for o in yazilacak] == [i] and olay is None

    yazilacak, olay = t.feed(_ornek(4, 9))
    assert olay == "tamamlandi"
    assert t.yakalama == 1
    assert t.durum == Trigger.BITTI, "rearm kapalıysa durmalı"


def test_kosul_dogru_kaldigi_surece_tekrar_tetiklenmez():
    t = Trigger(Expression("hata > 5", ["hata"]), pre=1, post=1, rearm=True)
    t.feed(_ornek(0, 0))
    _, olay = t.feed(_ornek(1, 9))
    assert olay == "tetiklendi"
    _, olay = t.feed(_ornek(2, 9))
    assert olay == "tamamlandi"

    for i in (3, 4, 5):
        yazilacak, olay = t.feed(_ornek(i, 9))
        assert olay is None and yazilacak == ()

    t.feed(_ornek(6, 0))
    _, olay = t.feed(_ornek(7, 9))
    assert olay == "tetiklendi", "koşul düşüp yükselince yeniden tetiklenmeli"


def test_yakalama_siniri():
    t = Trigger(Expression("hata > 5", ["hata"]), pre=0, post=1,
                rearm=True, max_captures=2)
    for tur in range(3):
        t.feed(_ornek(tur * 10, 0))
        t.feed(_ornek(tur * 10 + 1, 9))
        t.feed(_ornek(tur * 10 + 2, 9))
    assert t.yakalama == 2
    assert t.durum == Trigger.BITTI
    assert t.feed(_ornek(99, 9)) == ((), None), "bittikten sonra sessiz kalmalı"


def test_bozuk_deger_akisi_durdurmaz():
    t = Trigger(Expression("hata > 5", ["hata"]), pre=1, post=1)
    kotu = Sample(seq=0, t=0.0, values={"hata": None})
    assert t.feed(kotu) == ((), None)


def test_kart_yanit_vermiyorsa_acilmaz(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    rsp.hat_canli = False
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (rsp, None))

    s = Sampler([VarSpec("a", "u32", BASE)], elf="", hz=10.0)
    with pytest.raises(core.ProbeError, match="yanit vermiyor"):
        s.open()
    assert rsp.closed, "başarısız açılışta bağlantı kapatılmalı"


def test_nobetci_sessiz_kopmayi_yakalar(monkeypatch, bellek):
    monkeypatch.setattr(core, "WATCHDOG_S", 0.0)
    monkeypatch.setattr(core, "RETRY_INTERVAL_S", 0.0)

    rsp = FakeRsp(bellek)
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (rsp, None))
    s = Sampler([VarSpec("sayac", "u32", BASE)], elf="", hz=1000.0)
    s.open()

    rsp.hat_canli = False
    olaylar = []
    with pytest.raises(core.ProbeError):
        list(s.stream(limit=5, on_event=lambda t, d: olaylar.append(t)))
    assert "koptu" in olaylar


@pytest.mark.parametrize("kaynak, mesaj", [
    ("3", "en az bir degisken"),
    ("1 > 0", "en az bir degisken"),
    ("hata", "karsilastirma olmali"),
    ("hata + 5", "karsilastirma olmali"),
    ("abs(hata)", "karsilastirma olmali"),
])


def test_anlamsiz_kosul_reddedilir(kaynak, mesaj):
    with pytest.raises(ExpressionError, match=mesaj):
        Expression(kaynak, ADLAR)


def test_basta_dogru_olan_kosul_tetiklemez():
    t = Trigger(Expression("hata > 5", ["hata"]), pre=3, post=2)

    yazilacak, olay = t.feed(_ornek(0, 9))
    assert olay is None and yazilacak == ()

    t.feed(_ornek(1, 0))
    _, olay = t.feed(_ornek(2, 9))
    assert olay == "tetiklendi"


def test_akis_sirasinda_degisken_eklenebilir(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (rsp, None))

    s = Sampler([VarSpec("sayac", "u32", BASE)], elf="", hz=1000.0)
    s.open()
    assert set(s.read_once()) == {"sayac"}

    s.degiskenleri_degistir([
        VarSpec("sayac", "u32", BASE),
        VarSpec("sinus", "f32", BASE + 4),
    ])
    degerler = s.read_once()

    assert set(degerler) == {"sayac", "sinus"}
    assert degerler["sayac"] == 1883
    assert degerler["sinus"] == pytest.approx(0.5)
    assert not rsp.closed, "bağlantı korunmalı, yeniden kurulmamalı"


def test_degisken_cikarilinca_blok_daralir(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (rsp, None))

    s = Sampler([VarSpec("sayac", "u32", BASE),
                 VarSpec("hiz", "i16", BASE + 8)], elf="", hz=1000.0)
    s.open()
    genis = s._block[1]

    s.degiskenleri_degistir([VarSpec("sayac", "u32", BASE)])
    assert s._block[1] < genis
    assert set(s.read_once()) == {"sayac"}


def test_bos_degisken_kumesi_reddedilir(monkeypatch, bellek):
    rsp = FakeRsp(bellek)
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (rsp, None))
    s = Sampler([VarSpec("sayac", "u32", BASE)], elf="", hz=1000.0)
    s.open()
    with pytest.raises(ValueError, match="değişken seçilmedi"):
        s.degiskenleri_degistir([])


SATIR_SONU = chr(10)


def _kayit_yaz(yol, satirlar):
    metin = SATIR_SONU.join(json.dumps(x) for x in satirlar) + SATIR_SONU
    yol.write_text(metin, encoding="utf-8")


def test_csv_sema_degisimini_birlestirir(tmp_path):
    nd, csv = tmp_path / "k.ndjson", tmp_path / "k.csv"
    _kayit_yaz(nd, [
        {"type": "header", "vars": [{"name": "a", "type": "u32"}]},
        {"type": "sample", "seq": 0, "t": 0.0, "values": {"a": 1}},
        {"type": "vars", "vars": [{"name": "a"}, {"name": "b"}]},
        {"type": "sample", "seq": 1, "t": 0.01, "values": {"a": 2, "b": 0.5}},
        {"type": "sample", "seq": 2, "t": 0.02, "values": {"b": 0.6}},
        {"type": "end", "samples": 3},
    ])

    assert ndjson_to_csv(nd, csv) == 3
    satirlar = csv.read_text(encoding="utf-8").strip().split(SATIR_SONU)
    assert satirlar[0] == "t,seq,a,b"
    assert satirlar[1] == "0.000000,0,1,"
    assert satirlar[2] == "0.010000,1,2,0.5"
    assert satirlar[3] == "0.020000,2,,0.6", "olmayan değer boş kalmalı"


def test_csv_ornek_disi_satirlari_atlar(tmp_path):
    nd, csv = tmp_path / "k.ndjson", tmp_path / "k.csv"
    _kayit_yaz(nd, [
        {"type": "header", "vars": [{"name": "a", "type": "u32"}]},
        {"type": "link", "event": "koptu", "detail": "x"},
        {"type": "sample", "seq": 0, "t": 0.0, "values": {"a": 7}},
        {"type": "trigger", "event": "tetiklendi", "capture": 1},
        {"type": "end", "samples": 1},
    ])
    assert ndjson_to_csv(nd, csv) == 1
    satirlar = csv.read_text(encoding="utf-8").strip().split(SATIR_SONU)
    assert satirlar == ["t,seq,a", "0.000000,0,7"]


def test_csv_bos_kayitta_dosya_uretmez(tmp_path):
    nd, csv = tmp_path / "k.ndjson", tmp_path / "k.csv"
    _kayit_yaz(nd, [{"type": "header", "vars": []}, {"type": "end"}])
    assert ndjson_to_csv(nd, csv) == 0
    assert not csv.exists()


def test_csv_bozuk_satiri_atlar(tmp_path):
    nd, csv = tmp_path / "k.ndjson", tmp_path / "k.csv"
    tam = json.dumps({"type": "sample", "seq": 0, "t": 0.0,
                      "values": {"a": 1}})
    yarim = chr(123) + chr(34) + "type" + chr(34) + ":" + chr(34) + "sample"
    nd.write_text(tam + SATIR_SONU + yarim, encoding="utf-8")
    assert ndjson_to_csv(nd, csv) == 1


def test_baska_elf_secilince_eksikler_bulunur(monkeypatch):
    monkeypatch.setattr(core, "elf_degiskenleri",
                        lambda yol: {"yeni_a": (0x20000000, 4, "u32")})

    secili = [VarSpec("eski_x", "u32"), VarSpec("yeni_a", "u32")]
    tablo = core.elf_degiskenleri("yeni.elf")
    eksik = [v.name for v in secili if v.name not in tablo]
    assert eksik == ["eski_x"]


def test_eksik_degisken_akisi_engeller(monkeypatch, bellek):
    monkeypatch.setattr(core, "open_link", lambda *a, **k: (FakeRsp(bellek), None))
    monkeypatch.setattr(core, "elf_degiskenleri",
                        lambda yol: {"var_olan": (BASE, 4, "u32")})

    s = Sampler([VarSpec("var_olan", "u32"), VarSpec("olmayan", "u32")],
                elf="yeni.elf", hz=10.0)
    with pytest.raises(RuntimeError, match="bulunamayan semboller"):
        s.open()


def test_elf_gecmisi_en_yeniyi_basa_alir():
    p = Profile()
    for yol in ("a.elf", "b.elf", "c.elf"):
        elf_gecmisine_ekle(p, yol)
    assert p.son_elfler == ["c.elf", "b.elf", "a.elf"]


def test_elf_gecmisi_yinelemez():
    p = Profile()
    for yol in ("a.elf", "b.elf", "a.elf"):
        elf_gecmisine_ekle(p, yol)
    assert p.son_elfler == ["a.elf", "b.elf"], "aynı yol bir kez, en başta"


def test_elf_gecmisi_besle_sinirli():
    p = Profile()
    for i in range(9):
        elf_gecmisine_ekle(p, "%d.elf" % i)
    assert len(p.son_elfler) == core.ELF_GECMIS_SINIRI
    assert p.son_elfler[0] == "8.elf"


def test_elf_gecmisi_bos_yolu_almaz():
    p = Profile()
    elf_gecmisine_ekle(p, "")
    elf_gecmisine_ekle(p, "   ")
    assert p.son_elfler == []


def test_elf_gecmisi_profille_kaydedilir(tmp_path):
    yol = tmp_path / "p.json"
    p = Profile()
    elf_gecmisine_ekle(p, "x.elf")
    elf_gecmisine_ekle(p, "y.elf")
    p.save(yol)
    assert Profile.load(yol).son_elfler == ["y.elf", "x.elf"]


def _elf_onbellegi_temizle():
    core._ELF_ONBELLEK.clear()


def test_yarim_elf_anlasilir_hata_verir(tmp_path):
    _elf_onbellegi_temizle()
    yol = tmp_path / "yarim.elf"
    yol.write_bytes(bytes([0x7F]) + b"ELF" + bytes([1, 1, 1]) + bytes(40))

    with pytest.raises(ElfHatasi, match="okunamadı"):
        core.elf_degiskenleri(str(yol))


def test_olmayan_elf_anlasilir_hata_verir(tmp_path):
    _elf_onbellegi_temizle()
    with pytest.raises(ElfHatasi, match="okunamadı"):
        core.elf_degiskenleri(str(tmp_path / "yok.elf"))


def test_elf_okumasi_yeniden_denenir(tmp_path, monkeypatch):
    _elf_onbellegi_temizle()
    monkeypatch.setattr(core, "ELF_BEKLEME_S", 0.0)

    denemeler = {"n": 0}

    def bazen_patla(yol):
        denemeler["n"] += 1
        if denemeler["n"] == 1:
            raise struct.error("yarım dosya")
        return {"g_a": (0x20000000, 4)}

    monkeypatch.setattr(core, "elf_symbols", bazen_patla)
    monkeypatch.setattr(core.dwarf, "degisken_tipleri",
                        lambda yol: (_ for _ in ()).throw(core.dwarf.DwarfHatasi("yok")))

    tablo = core.elf_degiskenleri(str(tmp_path / "x.elf"))
    assert denemeler["n"] == 2, "ilk hatadan sonra yeniden denenmeli"
    assert tablo["g_a"] == (0x20000000, 4, None)
