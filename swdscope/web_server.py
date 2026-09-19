from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from swdscope_core import (
    ElfHatasi,
    Expression,
    ExpressionError,
    FanOut,
    NdjsonSink,
    UdpSink,
    Profile,
    Sampler,
    TYPES,
    Trigger,
    VarSpec,
    default_type_for,
    elf_degiskenleri,
    elf_gecmisine_ekle,
    detect_probe,
    identify_target,
    list_candidates,
    ProbeWatcher,
    _CLI,
    ndjson_to_csv,
    timestamped_path,
)

WEB_DIR = Path(__file__).parent / "web"

YAYIN_ARALIGI_S = 0.05

OZET_ARALIGI_S = 15.0

YUKLEME_ILGINC = re.compile(
    r'Download|Verify|File download|Error|error|RUNNING|Device name|'
    r'Memory Programming|Erasing|Download verified|%'
)

KUME_SINIRI = 60


class Uygulama:
    def __init__(self, profile: Profile, udp_port: int | None = None) -> None:
        self.profile = profile
        self.udp_port = udp_port
        self.aboneler: list[queue.Queue] = []
        self.kilit = threading.Lock()
        self.oturum: threading.Thread | None = None
        self.dur_bayragi = threading.Event()
        self.calisiyor = False
        self.probe = None
        self.hat_canli = True
        self.son_kayit = ""
        self.degisken_surumu = 0
        self.oynatma = None
        self.oynatma_hiz = 1.0
        self.oynatma_dur = threading.Event()
        self.yukleniyor = False
        self.aktif_sampler = None
        self.sayaclar = {"okunan": 0, "yazilan": 0, "yakalama": 0, "hiz": 0.0}
        self.gozcu = ProbeWatcher(self._kart_degisti, interval=3.0)
        self.gozcu.start()

    def _kart_degisti(self, probe) -> None:
        self.probe = probe
        if probe is not None:
            self.probe = identify_target(probe)
            self._olay("kart", "Kart algılandı: %s" % self.probe.label,
                       "basari")
        else:
            self._olay("kart", "Kart çıkarıldı", "uyari")
        self.yayinla("durum", self.durum())

    def abone_ol(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self.kilit:
            self.aboneler.append(q)
        return q

    def abonelikten_cik(self, q: queue.Queue) -> None:
        with self.kilit:
            if q in self.aboneler:
                self.aboneler.remove(q)

    def yayinla(self, tur: str, veri: dict) -> None:
        with self.kilit:
            hedefler = list(self.aboneler)
        for q in hedefler:
            try:
                q.put_nowait((tur, veri))
            except queue.Full:
                if tur == "ornek":
                    continue
                try:
                    q.get_nowait()
                    q.put_nowait((tur, veri))
                except (queue.Empty, queue.Full):
                    pass

    def kart_durumu(self, tani: bool = False) -> dict:
        if self.calisiyor or self.yukleniyor:
            return self._probe_json()
        self.probe = detect_probe()
        if self.probe and tani:
            self.probe = identify_target(self.probe)
        return self._probe_json()

    def _probe_json(self) -> dict:
        if self.calisiyor and not self.hat_canli:
            return {"bagli": False, "etiket": "bağlantı koptu"}
        if self.probe is None:
            return {"bagli": False, "etiket": "kart yok"}
        return {
            "bagli": True,
            "etiket": self.probe.label,
            "seri": self.probe.serial,
            "cihaz": self.probe.device,
            "gerilim": self.probe.voltage,
        }

    def _eksik_degiskenler(self) -> list[str]:
        if not self.profile.elf:
            return []
        try:
            tablo = elf_degiskenleri(self.profile.elf)
        except Exception:
            return []
        return [v.name for v in self.profile.variables if v.name not in tablo]

    def durum(self) -> dict:
        p = self.profile
        return {
            "kart": self._probe_json(),
            "calisiyor": self.calisiyor,
            "elf": p.elf,
            "son_elfler": p.son_elfler,
            "degiskenler": [{"ad": v.name, "tip": v.type} for v in p.variables],
            "hz": p.hz,
            "tetik": p.trigger,
            "pre": p.pre,
            "post": p.post,
            "max_captures": p.max_captures,
            "tipler": list(TYPES),
            "son_kayit": self.son_kayit,
            "sayaclar": self.sayaclar,
            "oynatma": self.oynatma or {"aktif": False},
            "yukleniyor": self.yukleniyor,
            "eksik": self._eksik_degiskenler(),
        }

    def baslat(self) -> tuple[bool, str]:
        if self.calisiyor:
            return False, "akış zaten sürüyor"
        if self.oynatma:
            return False, "önce kayıt oynatmayı durdur"
        if self.yukleniyor:
            return False, "yükleme sürerken akış başlatılamaz"
        if not self.profile.variables:
            return False, "önce izlenecek değişken seç"
        eksik = self._eksik_degiskenler()
        if eksik:
            return False, ("bu .elf'te olmayan değişkenler seçili: %s"
                           % ", ".join(eksik))
        self.dur_bayragi.clear()
        self.calisiyor = True
        self.oturum = threading.Thread(target=self._calis, daemon=True)
        self.oturum.start()
        return True, ""

    def durdur(self) -> None:
        self.dur_bayragi.set()
        self.oynatma_dur.set()

    def _olay(self, tur: str, mesaj: str, seviye: str = "bilgi") -> None:
        self.yayinla("olay", {"tur": tur, "mesaj": mesaj, "seviye": seviye,
                              "saat": time.strftime("%H:%M:%S")})

    def degisken_yaz(self, isim: str, deger) -> tuple[bool, str]:
        if self.yukleniyor:
            return False, "yükleme sürerken yazılamaz"
        if self.oynatma:
            return False, "kayıt oynatılırken yazılamaz (karta bağlı değiliz)"

        if self.calisiyor and self.aktif_sampler is not None:
            try:
                yazilan = self.aktif_sampler.yaz(isim, deger)
            except (ValueError, RuntimeError, OSError) as exc:
                self._olay("yazma", "Yazılamadı: %s" % exc, "hata")
                return False, str(exc)
            self._olay("yazma", "%s = %s yazıldı" % (isim, yazilan), "basari")
            return True, ""

        if self.calisiyor:
            return False, "akış henüz hazır değil, birazdan tekrar dene"
        gecici = None
        self.gozcu.pause()
        try:
            gecici = Sampler(self.profile.variables, self.profile.elf, 1.0)
            gecici.open()
            yazilan = gecici.yaz(isim, deger)
        except (ValueError, RuntimeError, OSError) as exc:
            self._olay("yazma", "Yazılamadı: %s" % exc, "hata")
            return False, str(exc)
        finally:
            if gecici is not None:
                gecici.close()
            self.gozcu.resume()
        self._olay("yazma", "%s = %s yazıldı" % (isim, yazilan), "basari")
        return True, ""

    def yukle(self, elf: str = "") -> tuple[bool, str]:
        if self.calisiyor:
            return False, "canlı akış sürerken karta yükleme yapılamaz"
        if self.yukleniyor:
            return False, "zaten bir yükleme sürüyor"
        yol = Path(elf or self.profile.elf)
        if not yol.is_file():
            return False, "dosya yok: %s" % yol
        if yol.suffix.lower() not in (".elf", ".hex", ".bin"):
            return False, "yalnızca .elf, .hex ve .bin yüklenebilir"

        self.yukleniyor = True
        threading.Thread(target=self._yukle, args=(yol,), daemon=True).start()
        return True, ""

    def _yukle(self, yol) -> None:
        self.gozcu.pause()
        self._olay("yukleme", "Karta yükleniyor: %s" % yol.name, "uyari")
        self.yayinla("durum", self.durum())

        komut = [_CLI, "-c", "port=SWD", "mode=UR",
                 "-w", str(yol), "-v", "-rst"]
        basarili = False
        try:
            sure = subprocess.Popen(komut, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace")
            son_yuzde = ""
            for satir in sure.stdout:
                satir = satir.strip()
                if not satir or not YUKLEME_ILGINC.search(satir):
                    continue
                if "%" in satir:
                    if satir == son_yuzde:
                        continue
                    son_yuzde = satir
                seviye = "hata" if "rror" in satir else "bilgi"
                self._olay("yukleme", satir[:160], seviye)
            basarili = sure.wait(timeout=300) == 0
        except (OSError, subprocess.SubprocessError) as exc:
            self._olay("yukleme", "Yükleme başarısız: %s" % exc, "hata")
        finally:
            self.yukleniyor = False
            self.gozcu.resume()
            self._olay("yukleme",
                       "Yükleme tamamlandı, kart yeniden başlatıldı"
                       if basarili else "Yükleme başarısız",
                       "basari" if basarili else "hata")
            if basarili:
                self._olay("yukleme",
                           "Not: adresler değişmiş olabilir, .elf yeniden okunacak",
                           "bilgi")
            self.yayinla("durum", self.durum())

    def oynat(self, ad: str, hiz: float) -> tuple[bool, str]:
        if self.calisiyor:
            return False, "canlı akış sürerken oynatma yapılamaz"
        if self.oynatma:
            return False, "zaten bir kayıt oynatılıyor"
        yol = Path(self.profile.out_dir) / Path(ad).name
        if not yol.is_file():
            return False, "kayıt bulunamadı: %s" % ad
        self.oynatma_hiz = max(0.0, hiz)
        self.oynatma_dur.clear()
        self.oynatma = {"aktif": True, "dosya": yol.name,
                        "hiz": self.oynatma_hiz, "toplam": 0,
                        "ilerleme": 0, "degiskenler": []}
        threading.Thread(target=self._oynat, args=(yol,), daemon=True).start()
        return True, ""

    def _oynat(self, yol) -> None:
        degiskenler, toplam = self._kayit_ozeti(yol)
        self.oynatma = {"aktif": True, "dosya": yol.name,
                        "hiz": self.oynatma_hiz, "toplam": toplam,
                        "ilerleme": 0, "degiskenler": degiskenler}
        self._olay("oynatma", "Oynatılıyor: %s (%d örnek)" % (yol.name, toplam),
                   "basari")
        self.yayinla("durum", self.durum())

        kume: list[dict] = []
        baslangic = time.perf_counter()
        duvar_hedef = 0.0
        onceki_t = None
        son_yayin = baslangic
        n = 0

        try:
            with open(yol, encoding="utf-8") as f:
                for satir in f:
                    if self.oynatma_dur.is_set():
                        break
                    if '"sample"' not in satir:
                        continue
                    try:
                        m = json.loads(satir)
                    except ValueError:
                        continue
                    if m.get("type") != "sample":
                        continue

                    hiz = self.oynatma_hiz
                    if onceki_t is not None and hiz > 0:
                        duvar_hedef += (m["t"] - onceki_t) / hiz
                        uyku = duvar_hedef - (time.perf_counter() - baslangic)
                        if uyku > 0:
                            self.oynatma_dur.wait(min(uyku, 0.05))
                            while (duvar_hedef - (time.perf_counter() - baslangic)
                                   > 0 and not self.oynatma_dur.is_set()
                                   and self.oynatma_hiz == hiz):
                                self.oynatma_dur.wait(0.01)
                    onceki_t = m["t"]

                    n += 1
                    kume.append({"t": m["t"], "seq": m["seq"], "d": m["values"]})

                    simdi = time.perf_counter()
                    if simdi - son_yayin >= YAYIN_ARALIGI_S:
                        self.oynatma["ilerleme"] = n
                        self.oynatma["hiz"] = self.oynatma_hiz
                        self.yayinla("ornek", {
                            "ornekler": _seyrelt(kume, KUME_SINIRI),
                            "sayaclar": {"okunan": n, "yazilan": 0,
                                         "yakalama": 0,
                                         "hiz": round(self.oynatma_hiz, 2)},
                            "oynatma": {"ilerleme": n, "toplam": toplam,
                                        "hiz": self.oynatma_hiz,
                                        "dosya": yol.name},
                        })
                        kume = []
                        son_yayin = simdi
        except OSError as exc:
            self._olay("hata", "Oynatma hatası: %s" % exc, "hata")
        finally:
            if kume:
                self.yayinla("ornek", {"ornekler": _seyrelt(kume, KUME_SINIRI),
                                       "sayaclar": {"okunan": n, "yazilan": 0,
                                                    "yakalama": 0, "hiz": 0}})
            self.oynatma = None
            self._olay("oynatma", "Oynatma bitti — %d örnek" % n, "bilgi")
            self.yayinla("durum", self.durum())

    def _kayit_ozeti(self, yol):
        adlar, gorulen, toplam = [], set(), 0
        try:
            with open(yol, encoding="utf-8") as f:
                for satir in f:
                    if '"sample"' not in satir:
                        continue
                    try:
                        m = json.loads(satir)
                    except ValueError:
                        continue
                    if m.get("type") != "sample":
                        continue
                    toplam += 1
                    for ad in m["values"]:
                        if ad not in gorulen:
                            gorulen.add(ad)
                            adlar.append({"ad": ad, "tip": "f64"})
        except OSError:
            pass
        return adlar, toplam

    def _kayit_ac(self, sampler: Sampler):
        nd_yol = timestamped_path(self.profile.out_dir, ".ndjson")
        nd = nd_yol.open("w", encoding="utf-8", newline="\n")
        sinks = [NdjsonSink(nd)]
        if self.udp_port:
            sinks.append(UdpSink(self.udp_port))
        sink = FanOut(sinks)
        sink.header(sampler.meta())
        self.son_kayit = str(nd_yol)
        return sink, nd, nd_yol

    def _degiskenleri_uygula(self, sampler, sink, tetik):
        p = self.profile
        try:
            sampler.degiskenleri_degistir(p.variables)
        except (ValueError, RuntimeError) as exc:
            self._olay("hata", "Değişken değişikliği uygulanamadı: %s" % exc,
                       "hata")
            return tetik

        sink.event({"type": "vars",
                    "vars": [{"name": v.name, "type": v.type,
                              "addr": "0x%08X" % v.addr} for v in p.variables]})

        if p.trigger:
            try:
                ifade = Expression(p.trigger, [v.name for v in p.variables])
                tetik = Trigger(ifade, pre=p.pre, post=p.post, rearm=True,
                                max_captures=p.max_captures)
            except ExpressionError as exc:
                tetik = None
                p.trigger = ""
                self._olay("tetik", "Tetikleyici kapatıldı: %s" % exc, "uyari")
        else:
            tetik = None

        self._olay("degisken", "İzlenen değişkenler: %s"
                   % ", ".join(v.name for v in p.variables), "basari")
        self.yayinla("durum", self.durum())
        return tetik

    def _calis(self) -> None:
        p = self.profile
        self.gozcu.pause()
        self.calisiyor = True
        self.hat_canli = True
        self.sayaclar = {"okunan": 0, "yazilan": 0, "yakalama": 0, "hiz": 0.0}

        tetik = None
        if p.trigger:
            try:
                ifade = Expression(p.trigger, [v.name for v in p.variables])
                tetik = Trigger(ifade, pre=p.pre, post=p.post, rearm=True,
                                max_captures=p.max_captures)
            except ExpressionError as exc:
                self._olay("hata", "Tetikleyici geçersiz: %s" % exc, "hata")
                self.calisiyor = False
                self.gozcu.resume()
                self.yayinla("durum", self.durum())
                return

        sampler = Sampler(p.variables, p.elf, p.hz)
        self.aktif_sampler = sampler
        self._olay("baglaniyor", "Karta bağlanılıyor...")
        try:
            sampler.open()
        except (OSError, RuntimeError) as exc:
            self._olay("hata", str(exc), "hata")
            self.calisiyor = False
            self.aktif_sampler = None
            self.gozcu.resume()
            self.yayinla("durum", self.durum())
            return

        surum = self.degisken_surumu
        try:
            sink, nd, nd_yol = self._kayit_ac(sampler)
        except OSError as exc:
            self._olay("hata", "Kayıt dosyası açılamadı: %s" % exc, "hata")
            sampler.close()
            self.calisiyor = False
            self.aktif_sampler = None
            self.gozcu.resume()
            self.yayinla("durum", self.durum())
            return

        self._olay("basladi", "Akış başladı → %s" % nd_yol.name, "basari")
        if tetik:
            self._olay("tetik", "Tetikleyici etkin: %s" % p.trigger, "uyari")
        self.yayinla("durum", self.durum())

        kume: list[dict] = []
        son_yayin = time.perf_counter()
        son_ozet = time.perf_counter()
        t0 = time.perf_counter()
        okunan = yazilan = 0
        neden = "kullanıcı durdurdu"

        def link_olayi(tur: str, ayrinti: str) -> None:
            seviye = {"koptu": "hata", "baglandi": "basari",
                      "vazgecildi": "hata"}.get(tur, "uyari")
            onek = {
                "koptu": "Bağlantı koptu",
                "deniyor": "Yeniden bağlanılıyor",
                "baglandi": "Yeniden bağlandı",
                "vazgecildi": "Vazgeçildi",
            }.get(tur, tur)
            self.hat_canli = tur == "baglandi"
            self._olay(tur, "%s — %s" % (onek, ayrinti), seviye)
            self.yayinla("durum", self.durum())
            sink.event({"type": "link", "event": tur, "detail": ayrinti,
                        "t": round(time.perf_counter() - t0, 3)})

        try:
            for ornek in sampler.stream(on_event=link_olayi):
                if self.dur_bayragi.is_set():
                    break

                if self.degisken_surumu != surum:
                    surum = self.degisken_surumu
                    tetik = self._degiskenleri_uygula(sampler, sink, tetik)
                    continue

                okunan += 1

                if tetik is None:
                    sink.sample(ornek)
                    yazilan += 1
                else:
                    yazilacak, tetik_olay = tetik.feed(ornek)
                    for o in yazilacak:
                        sink.sample(o)
                    yazilan += len(yazilacak)
                    if tetik_olay:
                        sira = (tetik.yakalama if tetik_olay == "tamamlandi"
                                else tetik.yakalama + 1)
                        sink.event({"type": "trigger", "event": tetik_olay,
                                    "capture": sira, "expr": p.trigger,
                                    "t": round(ornek.t, 3)})
                        self._olay(
                            "tetik",
                            ("TETİKLENDİ — %d. yakalama, öncesindeki %d örnekle"
                             % (sira, tetik.pre)) if tetik_olay == "tetiklendi"
                            else "%d. yakalama tamamlandı" % sira,
                            "basari" if tetik_olay == "tetiklendi" else "bilgi")
                        self.sayaclar["yakalama"] = tetik.yakalama
                        if tetik.durum == tetik.BITTI:
                            neden = "tetikleyici tamamlandı"
                            break

                kume.append({"t": round(ornek.t, 4), "seq": ornek.seq,
                             "d": ornek.values})

                simdi = time.perf_counter()
                if simdi - son_ozet >= OZET_ARALIGI_S:
                    son_ozet = simdi
                    self._olay("ilerleme",
                               "%.0f sn: %d okuma, %d kayıt%s"
                               % (simdi - t0, okunan, yazilan,
                                  ", %d yakalama" % tetik.yakalama
                                  if tetik else ""))
                if simdi - son_yayin >= YAYIN_ARALIGI_S:
                    gecen = simdi - t0
                    self.sayaclar.update(
                        okunan=okunan, yazilan=yazilan,
                        hiz=round(okunan / max(gecen, 1e-9), 1))
                    self.yayinla("ornek", {
                        "ornekler": _seyrelt(kume, KUME_SINIRI),
                        "sayaclar": dict(self.sayaclar),
                    })
                    kume = []
                    son_yayin = simdi
        except (OSError, RuntimeError) as exc:
            neden = str(exc)
            self._olay("hata", "Akış koptu: %s" % exc, "hata")
        finally:
            sure = time.perf_counter() - t0
            sink.end({"type": "end", "samples": yazilan, "sampled": okunan,
                      "seconds": round(sure, 3), "reason": neden})
            sampler.close()
            sink.close()
            nd.close()
            self.calisiyor = False
            self.aktif_sampler = None
            self.gozcu.resume()
            self.sayaclar.update(okunan=okunan, yazilan=yazilan)

            try:
                satir = ndjson_to_csv(nd_yol, nd_yol.with_suffix(".csv"))
            except OSError as exc:
                satir = 0
                self._olay("hata", "CSV üretilemedi: %s" % exc, "hata")

            self._olay("durdu",
                       "Durdu — %d okuma, %d kayıt, %.1f s · %s"
                       % (okunan, yazilan, sure, nd_yol.name), "bilgi")
            if satir:
                self._olay("csv", "CSV üretildi: %s (%d satır)"
                           % (nd_yol.with_suffix(".csv").name, satir),
                           "basari")
            self.yayinla("durum", self.durum())


def _sayi(deger, varsayilan: float) -> float:
    try:
        return float(deger)
    except (TypeError, ValueError):
        return varsayilan


def _seyrelt(kume: list, sinir: int) -> list:
    if len(kume) <= sinir:
        return kume
    adim = len(kume) / sinir
    return [kume[int(i * adim)] for i in range(sinir)]


class Istek(BaseHTTPRequestHandler):
    uygulama: Uygulama = None
    protocol_version = "HTTP/1.1"
    server_version = "SWDScope"

    def log_message(self, bicim, *args):
        pass

    def _json(self, veri, kod: int = 200) -> None:
        govde = json.dumps(veri, ensure_ascii=False).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(govde)

    def _dosya(self, yol: Path, tur: str) -> None:
        try:
            govde = yol.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", tur)
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(govde)

    def _govde(self) -> dict:
        uzunluk = int(self.headers.get("Content-Length", 0) or 0)
        if not uzunluk:
            return {}
        try:
            return json.loads(self.rfile.read(uzunluk).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_GET(self) -> None:
        yol = urlparse(self.path)
        sorgu = parse_qs(yol.query)
        u = self.uygulama

        if yol.path in ("/", "/index.html"):
            self._dosya(WEB_DIR / "index.html", "text/html; charset=utf-8")
        elif yol.path == "/api/durum":
            u.kart_durumu(tani=sorgu.get("tani", ["0"])[0] == "1")
            self._json(u.durum())
        elif yol.path == "/api/semboller":
            self._semboller(sorgu)
        elif yol.path == "/api/kayitlar":
            self._kayitlar()
        elif yol.path == "/api/indir":
            self._indir(sorgu)
        elif yol.path == "/api/akis":
            self._akis()
        else:
            self.send_error(404)

    def _semboller(self, sorgu) -> None:
        u = self.uygulama
        if not u.profile.elf:
            self._json({"hata": "önce .elf seç", "semboller": []}, 400)
            return
        try:
            satirlar = list_candidates(u.profile.elf)
        except ElfHatasi as exc:
            self._json({"hata": str(exc), "semboller": []}, 400)
            return
        except Exception as exc:
            self._json({"hata": "%s okunamadı: %s"
                        % (Path(u.profile.elf).name, exc),
                        "semboller": []}, 400)
            return
        ara = (sorgu.get("ara", [""])[0] or "").lower()
        sonuc = [
            {"ad": ad, "adres": "0x%08X" % adres, "boyut": boyut,
             "tip": tip or default_type_for(boyut),
             "kesin": bool(tip)}
            for adres, boyut, ad, tip in satirlar
            if not ara or ara in ad.lower()
        ]
        self._json({"semboller": sonuc[:300], "toplam": len(sonuc)})

    def _kayitlar(self) -> None:
        klasor = Path(self.uygulama.profile.out_dir)
        kayitlar = []
        if klasor.is_dir():
            for yol in sorted(klasor.glob("*.ndjson"), reverse=True)[:30]:
                csv = yol.with_suffix(".csv")
                kayitlar.append({
                    "ad": yol.name,
                    "boyut": yol.stat().st_size,
                    "csv": csv.name if csv.exists() else "",
                    "csv_boyut": csv.stat().st_size if csv.exists() else 0,
                })
        self._json({"kayitlar": kayitlar, "klasor": str(klasor.resolve())})

    def _kayit_yolu(self, ad: str):
        klasor = Path(self.uygulama.profile.out_dir).resolve()
        hedef = (klasor / Path(ad or "").name).resolve()
        if hedef.parent != klasor:
            return None
        return hedef

    def _kayit_sil(self, govde: dict) -> None:
        adlar = govde.get("adlar") or ([govde["ad"]] if govde.get("ad") else [])
        silinen, hatalar = [], []

        for ad in adlar:
            nd = self._kayit_yolu(ad)
            if nd is None or not nd.is_file():
                hatalar.append("bulunamadı: %s" % ad)
                continue
            if str(nd) == self.uygulama.son_kayit and self.uygulama.calisiyor:
                hatalar.append("akış sürerken bu kayıt silinemez: %s" % ad)
                continue
            for hedef in (nd, nd.with_suffix(".csv")):
                try:
                    hedef.unlink(missing_ok=True)
                except OSError as exc:
                    hatalar.append("%s: %s" % (hedef.name, exc))
            silinen.append(nd.name)

        if silinen:
            self.uygulama._olay(
                "kayit", "Silindi: %s" % ", ".join(silinen), "uyari")
        for h in hatalar:
            self.uygulama._olay("kayit", h, "hata")
        self._json({"tamam": not hatalar, "silinen": silinen,
                    "hatalar": hatalar})

    def _indir(self, sorgu) -> None:
        hedef = self._kayit_yolu((sorgu.get("ad", [""])[0] or "").strip())
        if hedef is None or not hedef.is_file():
            self.send_error(404)
            return
        govde = hedef.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % hedef.name)
        self.send_header("Content-Length", str(len(govde)))
        self.end_headers()
        self.wfile.write(govde)

    def _akis(self) -> None:
        u = self.uygulama
        q = u.abone_ol()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self._sse("durum", u.durum())
            while True:
                try:
                    tur, veri = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse(tur, veri)
        except (OSError, ValueError):
            pass
        finally:
            u.abonelikten_cik(q)

    def _sse(self, tur: str, veri) -> None:
        paket = "event: %s\ndata: %s\n\n" % (
            tur, json.dumps(veri, ensure_ascii=False))
        self.wfile.write(paket.encode("utf-8"))
        self.wfile.flush()

    def do_POST(self) -> None:
        yol = urlparse(self.path).path
        u = self.uygulama
        govde = self._govde()

        if yol == "/api/ayarla":
            self._ayarla(govde)
        elif yol == "/api/baslat":
            tamam, hata = u.baslat()
            self._json({"tamam": tamam, "hata": hata}, 200 if tamam else 400)
        elif yol == "/api/durdur":
            u.durdur()
            self._json({"tamam": True})
        elif yol == "/api/tetik-dene":
            self._tetik_dene(govde)
        elif yol == "/api/elf-gecmis-sil":
            u.profile.son_elfler = []
            u.profile.save()
            u._olay("elf", "Kaydedilen .elf yolları silindi", "uyari")
            u.yayinla("durum", u.durum())
            self._json({"tamam": True})
        elif yol == "/api/yaz":
            tamam, hata = u.degisken_yaz(govde.get("ad", ""),
                                         govde.get("deger"))
            self._json({"tamam": tamam, "hata": hata},
                       200 if tamam else 400)
        elif yol == "/api/yukle":
            tamam, hata = u.yukle(govde.get("elf", ""))
            self._json({"tamam": tamam, "hata": hata},
                       200 if tamam else 400)
        elif yol == "/api/oynat":
            tamam, hata = u.oynat(govde.get("ad", ""),
                                  _sayi(govde.get("hiz", 1.0), 1.0))
            self._json({"tamam": tamam, "hata": hata},
                       200 if tamam else 400)
        elif yol == "/api/oynat-hiz":
            u.oynatma_hiz = max(0.0, min(200.0,
                                         _sayi(govde.get("hiz", 1.0), 1.0)))
            if u.oynatma:
                u.oynatma["hiz"] = u.oynatma_hiz
            self._json({"tamam": True, "hiz": u.oynatma_hiz})
        elif yol == "/api/kayit-sil":
            self._kayit_sil(govde)
        elif yol == "/api/kaydet":
            self._json({"tamam": True, "yol": str(u.profile.save())})
        else:
            self.send_error(404)

    def _ayarla(self, govde: dict) -> None:
        u = self.uygulama
        p = u.profile
        hatalar = []

        if "elf" in govde:
            yeni = (govde["elf"] or "").strip().strip('"')
            if yeni and not Path(yeni).exists():
                hatalar.append("dosya yok: %s" % yeni)
            else:
                p.elf = yeni
                elf_gecmisine_ekle(p, yeni)

        if "degiskenler" in govde:
            eski_kume = [(v.name, v.type) for v in p.variables]
            p.variables = [
                VarSpec(name=d["ad"], type=d.get("tip", "u32"))
                for d in govde["degiskenler"]
                if isinstance(d, dict) and isinstance(d.get("ad"), str)
                and d.get("tip", "u32") in TYPES
            ]
            if [(v.name, v.type) for v in p.variables] != eski_kume:
                u.degisken_surumu += 1

        if "hz" in govde:
            try:
                p.hz = max(0.0, min(2000.0, float(govde["hz"])))
            except (TypeError, ValueError):
                hatalar.append("hız sayı olmalı")

        for alan, en_az in (("pre", 0), ("post", 1), ("max_captures", 0)):
            if alan in govde:
                try:
                    setattr(p, alan, max(en_az, int(govde[alan])))
                except (TypeError, ValueError):
                    hatalar.append("%s sayı olmalı" % alan)

        if "tetik" in govde:
            ifade = (govde["tetik"] or "").strip()
            if ifade:
                try:
                    Expression(ifade, [v.name for v in p.variables])
                except ExpressionError as exc:
                    hatalar.append(str(exc))
                    ifade = p.trigger
            p.trigger = ifade

        p.save()
        u.yayinla("durum", u.durum())
        self._json({"tamam": not hatalar, "hatalar": hatalar,
                    "durum": u.durum()})

    def _tetik_dene(self, govde: dict) -> None:
        u = self.uygulama
        ifade = (govde.get("ifade") or "").strip()
        if not ifade:
            self._json({"gecerli": True, "mesaj": "kapalı"})
            return
        try:
            Expression(ifade, [v.name for v in u.profile.variables])
        except ExpressionError as exc:
            self._json({"gecerli": False, "mesaj": str(exc)})
            return
        self._json({"gecerli": True, "mesaj": "geçerli"})


def calistir(port: int = 8730, tarayici_ac: bool = True,
             udp_port: int | None = None) -> int:
    if not (WEB_DIR / "index.html").exists():
        print("web/index.html bulunamadı: %s" % WEB_DIR)
        return 1

    Istek.uygulama = Uygulama(Profile.load(), udp_port)
    try:
        sunucu = ThreadingHTTPServer(("127.0.0.1", port), Istek)
    except OSError as exc:
        print("%d portu acilamadi: %s" % (port, exc))
        print("  * Bu arac baska bir pencerede zaten acik olabilir:")
        print("    http://127.0.0.1:%d adresini dene." % port)
        print("  * Ya da baska bir port sec:  python swdscope.py --web 8731")
        return 1
    sunucu.daemon_threads = True

    adres = "http://127.0.0.1:%d" % port
    print("SWDScope web arayüzü: %s" % adres)
    if udp_port:
        print("  UDP çıkışı: 127.0.0.1:%d" % udp_port)
    print("  (durdurmak için Ctrl+C)")

    if tarayici_ac:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(adres)).start()

    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\nkapatılıyor...")
    finally:
        Istek.uygulama.durdur()
        sunucu.shutdown()
        sunucu.server_close()
    return 0
