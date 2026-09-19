from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from swdscope_core import (
    CsvSink,
    ElfHatasi,
    Expression,
    ExpressionError,
    FanOut,
    NdjsonSink,
    Profile,
    ProbeInfo,
    ProbeWatcher,
    Sampler,
    TYPES,
    Trigger,
    UdpSink,
    VarSpec,
    default_type_for,
    detect_probe,
    identify_target,
    list_candidates,
    timestamped_path,
)

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"
CLEAR_LINE = "\033[2K"
UP = "\033[A"


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


class App:
    def __init__(self) -> None:
        self.profile = Profile.load()
        self.probe: ProbeInfo | None = None
        self.identified = False
        self.watcher = ProbeWatcher(self._on_probe_change)

    def _on_probe_change(self, probe: ProbeInfo | None) -> None:
        self.probe = probe
        self.identified = False

    def connect(self) -> None:
        print("\nKart aranıyor...")
        probe = detect_probe()
        if probe is None:
            print(c("Kart bulunamadı.", RED))
            print(DIM + "  * USB kablosu takılı mı?" + RESET)
            print(DIM + "  * CubeIDE debug oturumu veya CubeMonitor açıksa "
                  "kapat; probe tek istemci kabul eder." + RESET)
            return
        print("Tanınıyor...")
        self.probe = identify_target(probe)
        self.identified = True
        print(c("Bağlandı: " + self.probe.label, GREEN))

    def choose_elf(self) -> None:
        print("\n" + BOLD + "Proje dosyası (.elf)" + RESET)
        if self.profile.elf:
            print(DIM + "  şu an: " + self.profile.elf + RESET)

        adaylar = self._find_elfs()
        if adaylar:
            print("\nBulunanlar:")
            for i, p in enumerate(adaylar, 1):
                yas = time.strftime("%d.%m %H:%M", time.localtime(p.stat().st_mtime))
                print(f"  [{i}] {p}  {DIM}({yas}){RESET}")
            print("  [0] Yolu elle yaz")
            secim = input("\nSeçim: ").strip()
            if secim.isdigit() and 1 <= int(secim) <= len(adaylar):
                self._set_elf(adaylar[int(secim) - 1])
                return
            if secim != "0":
                return

        yol = input("\n.elf dosyasının tam yolu: ").strip().strip('"')
        if yol:
            self._set_elf(Path(yol))

    def _find_elfs(self) -> list[Path]:
        bulunan: list[Path] = []
        for kok in (Path.cwd(), Path.cwd().parent):
            try:
                bulunan += list(kok.glob("**/Debug/*.elf"))
                bulunan += list(kok.glob("**/Release/*.elf"))
            except OSError:
                continue
        benzersiz = {p.resolve(): p for p in bulunan}
        return sorted(benzersiz.values(), key=lambda p: -p.stat().st_mtime)[:9]

    def _set_elf(self, path: Path) -> None:
        if not path.exists():
            print(c("Dosya yok: %s" % path, RED))
            return
        try:
            sayi = len(list_candidates(str(path)))
        except (OSError, ValueError, ElfHatasi) as exc:
            print(c("Okunamadı: %s" % exc, RED))
            return
        self.profile.elf = str(path)
        print(c("Seçildi: %s  (%d RAM değişkeni)" % (path.name, sayi), GREEN))
        if self.profile.variables:
            print(DIM + "  Kayıtlı değişken seçimin korundu." + RESET)

    def choose_variables(self) -> None:
        if not self.profile.elf:
            print(c("\nÖnce proje dosyasını seç.", YELLOW))
            return

        try:
            adaylar = list_candidates(self.profile.elf)
        except (OSError, ValueError, ElfHatasi) as exc:
            print(c("\n.elf okunamadı: %s" % exc, RED))
            return

        filtre = ""
        while True:
            gosterilen = [
                r for r in adaylar if not filtre or filtre.lower() in r[2].lower()
            ]
            print("\n" + BOLD + "Değişken seçimi" + RESET)
            if filtre:
                print(DIM + "  süzgeç: %s  (%d eşleşme)" % (filtre, len(gosterilen)) + RESET)
            self._print_selected(adaylar)

            print()
            for i, (addr, size, name, tip) in enumerate(gosterilen[:20], 1):
                isaret = "*" if any(v.name == name for v in self.profile.variables) else " "
                etiket = tip or default_type_for(size) + "?"
                print(f" {isaret}[{i:2d}] 0x{addr:08X} {size:>4}B {etiket:<5} {name}")
            if len(gosterilen) > 20:
                print(DIM + "  ... %d tane daha, süzmek için /kelime yaz"
                      % (len(gosterilen) - 20) + RESET)

            print(DIM + "\n  numara: ekle/çıkar   /kelime: süz   t: tip değiştir"
                  "   s: sil   Enter: bitir" + RESET)
            girdi = input("> ").strip()

            if not girdi:
                return
            if girdi.startswith("/"):
                filtre = girdi[1:]
                continue
            if girdi == "s":
                self.profile.variables.clear()
                continue
            if girdi == "t":
                self._change_type()
                continue
            if girdi.isdigit() and 1 <= int(girdi) <= min(20, len(gosterilen)):
                addr, size, name, tip = gosterilen[int(girdi) - 1]
                self._toggle(name, size, tip)
                continue
            print(c("Anlaşılmadı.", YELLOW))

    def _toggle(self, name: str, size: int, tip: str | None = None) -> None:
        mevcut = next((v for v in self.profile.variables if v.name == name), None)
        if mevcut:
            self.profile.variables.remove(mevcut)
        else:
            self.profile.variables.append(
                VarSpec(name=name, type=tip or default_type_for(size)))

    def _print_selected(self, adaylar=None) -> None:
        if not self.profile.variables:
            print(DIM + "  seçili değişken yok" + RESET)
            return
        print("  seçili: " + ", ".join(
            f"{v.name}:{v.type}" for v in self.profile.variables))

        if adaylar is None:
            return
        var_olanlar = {r[2] for r in adaylar}
        eksik = [v.name for v in self.profile.variables
                 if v.name not in var_olanlar]
        if eksik:
            print(c("  bu .elf'te YOK: " + ", ".join(eksik), RED))
            print(DIM + "  akış başlamaz — 's' ile hepsini temizle" + RESET)

    def _change_type(self) -> None:
        if not self.profile.variables:
            return
        print()
        for i, v in enumerate(self.profile.variables, 1):
            print(f"  [{i}] {v.name}  şu an: {v.type}")
        secim = input("Hangisi? ").strip()
        if not secim.isdigit() or not 1 <= int(secim) <= len(self.profile.variables):
            return
        v = self.profile.variables[int(secim) - 1]
        print("  tipler: " + ", ".join(TYPES))
        yeni = input(f"{v.name} için yeni tip: ").strip()
        if yeni in TYPES:
            v.type = yeni
            print(c("  %s -> %s" % (v.name, yeni), GREEN))
        else:
            print(c("  bilinmeyen tip", YELLOW))

    def trigger_menu(self) -> None:
        adlar = [v.name for v in self.profile.variables]
        if not adlar:
            print()
            print(c("Önce izlenecek değişkenleri seç.", YELLOW))
            return

        print()
        print(BOLD + "Tetikleyici" + RESET)
        print(DIM + "  Koşul sağlandığı anda, ÖNCESİNDEKİ örneklerle birlikte"
              " kaydedilir." + RESET)
        print(DIM + "  Kullanılabilir değişkenler: " + ", ".join(adlar) + RESET)
        print(DIM + "  Örnekler:  hata > 5   |   abs(fark) > 0.5 and hiz < 10"
              + RESET)
        print(DIM + "  Boş bırakıp Enter: tetikleyiciyi kapat (her şey kaydedilir)"
              + RESET)
        if self.profile.trigger:
            print("  şu an: " + c(self.profile.trigger, GREEN))

        print()
        ifade = input("Koşul: ").strip()
        if not ifade:
            if self.profile.trigger:
                self.profile.trigger = ""
                print(c("Tetikleyici kapatıldı.", GREEN))
            return

        try:
            Expression(ifade, adlar)
        except ExpressionError as exc:
            print(c("Kabul edilmedi: %s" % exc, RED))
            return

        self.profile.trigger = ifade
        print(c("Koşul kabul edildi.", GREEN))

        pre = input(f"Öncesinden kaç örnek saklansın [{self.profile.pre}]: ").strip()
        if pre.isdigit():
            self.profile.pre = max(0, min(100000, int(pre)))
        post = input(f"Sonrasından kaç örnek [{self.profile.post}]: ").strip()
        if post.isdigit():
            self.profile.post = max(1, min(100000, int(post)))
        kez = input(f"Kaç yakalama sonra dursun (0 = sınırsız) "
                    f"[{self.profile.max_captures}]: ").strip()
        if kez.isdigit():
            self.profile.max_captures = max(0, int(kez))

        print(DIM + "  her yakalama %d + %d = %d örnek tutar"
              % (self.profile.pre, self.profile.post,
                 self.profile.pre + self.profile.post) + RESET)

    def _tetikleyici_kur(self):
        if not self.profile.trigger:
            return None
        adlar = [v.name for v in self.profile.variables]
        try:
            ifade = Expression(self.profile.trigger, adlar)
        except ExpressionError as exc:
            print(c("Tetikleyici geçersiz, yok sayıldı: %s" % exc, YELLOW))
            return None
        return Trigger(ifade, pre=self.profile.pre, post=self.profile.post,
                       rearm=True, max_captures=self.profile.max_captures)

    def settings(self) -> None:
        print("\n" + BOLD + "Ayarlar" + RESET)
        print(DIM + "  0 = serbest koşu (elden geldiğince hızlı)" + RESET)
        hz = input(f"Örnekleme hızı [{self.profile.hz:g} Hz]: ").strip()
        if hz:
            try:
                self.profile.hz = max(0.0, min(2000.0, float(hz)))
            except ValueError:
                print(c("Sayı değil, değiştirilmedi.", YELLOW))

        klasor = input(f"Kayıt klasörü [{self.profile.out_dir}]: ").strip()
        if klasor:
            self.profile.out_dir = klasor

    def live(self) -> None:
        if not self.profile.variables:
            print(c("\nÖnce izlenecek değişkenleri seç.", YELLOW))
            return

        self.watcher.pause()
        try:
            self._run_live()
        finally:
            self.watcher.resume()

    def _run_live(self) -> None:
        sampler = Sampler(self.profile.variables, self.profile.elf, self.profile.hz)

        nd_path = timestamped_path(self.profile.out_dir, ".ndjson")
        csv_path = nd_path.with_suffix(".csv")
        nd_file = nd_path.open("w", encoding="utf-8", newline="\n")
        csv_file = csv_path.open("w", encoding="utf-8", newline="\n")

        sink = FanOut([NdjsonSink(nd_file), CsvSink(csv_file)])

        print("\nBağlanılıyor...")
        try:
            sampler.open()
        except (OSError, RuntimeError) as exc:
            print(c("Bağlanamadı: %s" % exc, RED))
            nd_file.close()
            csv_file.close()
            nd_path.unlink(missing_ok=True)
            csv_path.unlink(missing_ok=True)
            return

        meta = sampler.meta()
        sink.header(meta)

        print(c("Akış başladı", GREEN) + DIM + "  (Ctrl+C ile dur)" + RESET)
        print(DIM + "  ndjson: %s" % nd_path + RESET)
        print(DIM + "  csv   : %s" % csv_path + RESET)
        print()

        satir_sayisi = len(self.profile.variables) + 2
        for _ in range(satir_sayisi):
            print()

        seq = 0
        yazilan = 0
        t0 = time.perf_counter()
        neden = "kullanıcı durdurdu"

        def olay(tur: str, ayrinti: str) -> None:
            sink.event({"type": "link", "event": tur, "detail": ayrinti,
                        "t": round(time.perf_counter() - t0, 3)})
            if tur == "koptu":
                print()
                print(c("  bağlantı koptu: %s" % ayrinti, RED))
                print(DIM + "  yeniden deneniyor... (Ctrl+C ile vazgeç)" + RESET)
            elif tur == "baglandi":
                print(c("  %s, akış sürüyor" % ayrinti, GREEN))
                for _ in range(satir_sayisi):
                    print()
            elif tur == "vazgecildi":
                print(c("  %s" % ayrinti, RED))

        tetik = self._tetikleyici_kur()
        if tetik:
            print(c("  tetik : " + self.profile.trigger, YELLOW)
                  + DIM + "  (yalnızca yakalamalar kaydedilir)" + RESET)

        try:
            for s in sampler.stream(on_event=olay):
                if tetik is None:
                    sink.sample(s)
                    yazilan += 1
                else:
                    yazilacak, tetik_olay = tetik.feed(s)
                    for ornek in yazilacak:
                        sink.sample(ornek)
                    yazilan += len(yazilacak)
                    if tetik_olay:
                        self._tetik_bildir(sink, tetik, tetik_olay,
                                           t0, satir_sayisi)
                        if tetik.durum == tetik.BITTI:
                            neden = "tetikleyici tamamlandı"
                            break
                seq = s.seq
                if s.seq % max(1, int(self.profile.hz / 10)) == 0:
                    self._draw(s, satir_sayisi, tetik, t0)
        except KeyboardInterrupt:
            pass
        except (OSError, RuntimeError) as exc:
            neden = str(exc)
            print(c("\nAkış koptu: %s" % exc, RED))
        finally:
            sure = time.perf_counter() - t0
            sink.end({
                "type": "end",
                "samples": yazilan,
                "sampled": seq + 1,
                "seconds": round(sure, 3),
                "reason": neden,
            })
            sampler.close()
            sink.close()
            nd_file.close()
            csv_file.close()

        print()
        print(c("Durdu.", GREEN)
              + f" {seq + 1} okuma / {sure:.1f} s"
              + f" = {(seq + 1) / max(sure, 1e-9):.1f} okuma/s"
              + (f"   kayda yazılan: {yazilan}" if yazilan != seq + 1 else ""))
        print(DIM + "  %s" % nd_path + RESET)

    def _tetik_bildir(self, sink, tetik, olay: str, t0: float,
                      satir_sayisi: int) -> None:
        sira = tetik.yakalama if olay == "tamamlandi" else tetik.yakalama + 1
        sink.event({"type": "trigger", "event": olay, "capture": sira,
                    "expr": self.profile.trigger,
                    "t": round(time.perf_counter() - t0, 3)})
        if olay == "tetiklendi":
            print()
            print(c("  >>> TETİKLENDİ (%d. yakalama) - öncesindeki %d örnekle"
                    " birlikte kaydedildi" % (sira, tetik.pre), GREEN))
            for _ in range(satir_sayisi):
                print()

    def _draw(self, s, satir_sayisi: int, tetik, t0: float) -> None:
        sys.stdout.write(UP * satir_sayisi)
        hiz = (s.seq + 1) / max(time.perf_counter() - t0, 1e-9)
        sys.stdout.write(CLEAR_LINE + f"  {'DEĞİŞKEN':<24}{'DEĞER':>18}\n")
        sys.stdout.write(CLEAR_LINE + "  " + "-" * 42 + "\n")
        for v in self.profile.variables:
            deger = s.values.get(v.name, 0)
            metin = f"{deger:>18.6f}" if v.type in ("f32", "f64") else f"{deger:>18d}"
            sys.stdout.write(CLEAR_LINE + f"  {v.name:<24}{metin}\n")
        durum = ""
        if tetik is not None:
            durum = "   tetik: %s (%d yakalama)" % (tetik.durum, tetik.yakalama)
        sys.stdout.write(
            CLEAR_LINE + DIM
            + f"  örnek {s.seq + 1}   t={s.t:7.2f}s   {hiz:5.1f} örnek/s{durum}"
            + RESET + chr(10))
        sys.stdout.flush()

    def _status_line(self) -> str:
        if self.probe is None:
            return c("● kart yok", RED)
        if self.identified:
            return c("● " + self.probe.label, GREEN)
        return c("● kart algılandı (tanınmadı)", YELLOW)

    def menu(self) -> None:
        self.watcher.start()
        print(BOLD + "\nSWDScope" + RESET + DIM + " — STM32 canlı veri terminali" + RESET)
        print(DIM + "Kart otomatik algılanır; takıp çıkarabilirsin." + RESET)

        try:
            while True:
                degisken = (", ".join(v.name for v in self.profile.variables)
                            or DIM + "seçilmedi" + RESET)
                elf = Path(self.profile.elf).name if self.profile.elf else (
                    DIM + "seçilmedi" + RESET)

                print("\n" + "-" * 58)
                print("  durum : " + self._status_line())
                print("  proje : " + elf)
                print("  izlem : " + degisken)
                print("  hız   : %g Hz" % self.profile.hz)
                if self.profile.trigger:
                    print("  tetik : %s  (%d önce + %d sonra)" % (
                        self.profile.trigger, self.profile.pre,
                        self.profile.post))
                print("-" * 58)
                print("  [1] Bağlan / kartı tanı")
                print("  [2] Proje dosyası seç (.elf)")
                print("  [3] Değişken seç")
                print("  [4] Canlı veriyi çek")
                print("  [5] Ayarlar (hız, klasör)")
                print("  [6] Tetikleyici (koşullu kayıt)")
                print("  [7] Profili kaydet")
                print("  [0] Çıkış")

                secim = input("\n> ").strip()
                if secim == "1":
                    self.connect()
                elif secim == "2":
                    self.choose_elf()
                elif secim == "3":
                    self.choose_variables()
                elif secim == "4":
                    self.live()
                elif secim == "5":
                    self.settings()
                elif secim == "6":
                    self.trigger_menu()
                elif secim == "7":
                    p = self.profile.save()
                    print(c("Kaydedildi: %s" % p, GREEN))
                elif secim in ("0", "q"):
                    break
                else:
                    print(c("Geçersiz seçim.", YELLOW))
        except (KeyboardInterrupt, EOFError):
            print()
        finally:
            self.watcher.stop()
            self.profile.save()
            print(DIM + "Profil kaydedildi. Görüşürüz." + RESET)


def run_stream(args) -> int:
    profile = Profile.load()
    if args.elf:
        profile.elf = args.elf
    if args.hz is not None:
        profile.hz = args.hz
    if args.trigger is not None:
        profile.trigger = args.trigger
    if args.pre is not None:
        profile.pre = max(0, args.pre)
    if args.post is not None:
        profile.post = max(1, args.post)
    if not profile.variables:
        print("Profilde değişken yok. Önce 'python swdscope.py' ile seç.",
              file=sys.stderr)
        return 1

    tetik = None
    if profile.trigger:
        try:
            ifade = Expression(profile.trigger,
                               [v.name for v in profile.variables])
        except ExpressionError as exc:
            print("Tetikleyici geçersiz: %s" % exc, file=sys.stderr)
            return 1
        tetik = Trigger(ifade, pre=profile.pre, post=profile.post,
                        rearm=True, max_captures=profile.max_captures)
        print("tetikleyici: %s (%d önce + %d sonra)"
              % (profile.trigger, profile.pre, profile.post),
              file=sys.stderr)

    sampler = Sampler(profile.variables, profile.elf, profile.hz)
    sinks: list = [NdjsonSink(sys.stdout)]
    if args.udp:
        sinks.append(UdpSink(args.udp))
    sink = FanOut(sinks)

    try:
        sampler.open()
    except (OSError, RuntimeError) as exc:
        print("Bağlanamadı: %s" % exc, file=sys.stderr)
        return 1

    sink.header(sampler.meta())
    seq = 0
    yazilan = 0
    t0 = time.perf_counter()
    neden = "bitti"
    kod = 0
    t_bas = time.perf_counter()

    def olay(tur: str, ayrinti: str) -> None:
        sink.event({"type": "link", "event": tur, "detail": ayrinti,
                    "t": round(time.perf_counter() - t_bas, 3)})
        print("[%s] %s" % (tur, ayrinti), file=sys.stderr, flush=True)

    try:
        for s in sampler.stream(limit=args.count, on_event=olay):
            if tetik is None:
                sink.sample(s)
                yazilan += 1
            else:
                yazilacak, tetik_olay = tetik.feed(s)
                for ornek in yazilacak:
                    sink.sample(ornek)
                yazilan += len(yazilacak)
                if tetik_olay:
                    sira = (tetik.yakalama if tetik_olay == "tamamlandi"
                            else tetik.yakalama + 1)
                    sink.event({"type": "trigger", "event": tetik_olay,
                                "capture": sira, "expr": profile.trigger,
                                "t": round(s.t, 3)})
                    print("[tetik] %s (%d. yakalama)" % (tetik_olay, sira),
                          file=sys.stderr, flush=True)
                    if tetik.durum == tetik.BITTI:
                        neden = "tetikleyici tamamlandı"
                        break
            seq = s.seq
    except KeyboardInterrupt:
        neden = "kullanıcı durdurdu"
    except (OSError, RuntimeError) as exc:
        neden = str(exc)
        kod = 1
        print("Akış koptu: %s" % exc, file=sys.stderr)
    finally:
        sink.end({
            "type": "end",
            "samples": yazilan,
            "sampled": seq + 1,
            "seconds": round(time.perf_counter() - t0, 3),
            "reason": neden,
        })
        sampler.close()
        sink.close()
    return kod


def run_list(args) -> int:
    profile = Profile.load()
    elf = args.elf or profile.elf
    if not elf:
        print("`.elf` yolu gerekli: --elf ile ver.", file=sys.stderr)
        return 1
    try:
        rows = list_candidates(elf)
    except (OSError, ValueError, ElfHatasi) as exc:
        print(".elf okunamadı: %s" % exc, file=sys.stderr)
        return 1
    print("%d RAM değişkeni: %s\n" % (len(rows), elf))
    print("  ADRES        BAYT  TİP    KAYNAK  İSİM")
    for addr, size, name, tip in rows:
        print("  0x%08X %5d  %-6s %-7s %s"
              % (addr, size, tip or default_type_for(size),
                 "DWARF" if tip else "tahmin", name))
    return 0


def _setup_console() -> None:
    for akis in (sys.stdout, sys.stderr):
        try:
            akis.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except (OSError, AttributeError):
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="swdscope", description="STM32 canlı veri terminali (SWD / ST-LINK)")
    ap.add_argument("--stream", action="store_true",
                    help="menüsüz mod: NDJSON stdout'a")
    ap.add_argument("--web", nargs="?", const=8730, type=int,
                    metavar="PORT",
                    help="tarayıcı arayüzünü aç (varsayılan port 8730)")
    ap.add_argument("--list", action="store_true",
                    help=".elf içindeki RAM değişkenlerini listele")
    ap.add_argument("--elf", help="proje .elf yolu (profildekini geçersiz kılar)")
    ap.add_argument("--hz", type=float,
                help="örnekleme hızı (0 = serbest koşu, elden geldiğince)")
    ap.add_argument("--udp", type=int,
                    help="NDJSON satırlarını bu UDP portuna da yolla (--stream ve --web)")
    ap.add_argument("--trigger", metavar="KOSUL",
                    help="koşullu kayıt: profildekini geçersiz kılar"
                         " (örn. 'hata > 5'). Boş dize kapatır.")
    ap.add_argument("--pre", type=int, help="tetik öncesi saklanan örnek")
    ap.add_argument("--post", type=int, help="tetik sonrası örnek")
    ap.add_argument("--count", type=int, default=0,
                    help="bu kadar örnek alıp dur (0 = sonsuz)")
    args = ap.parse_args(argv)

    _setup_console()

    if args.web is not None:
        from web_server import calistir
        return calistir(port=args.web, udp_port=args.udp)
    if args.list:
        return run_list(args)
    if args.stream:
        return run_stream(args)

    App().menu()
    return 0


if __name__ == "__main__":
    sys.exit(main())
