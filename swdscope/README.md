# SWDScope

STM32 kartlarındaki değişkenleri **ST-LINK üzerinden, çekirdeği durdurmadan**
canlı okuyan; ekranda gösteren, dosyaya kaydeden ve istersen kendi uygulamana
akıtan araç.

UART, USB-seri dönüştürücü ya da firmware'e eklenecek kod gerekmez. Kartta
ST-LINK kablosu dışında kablo yoktur. STM32CubeIDE'deki **Live Expressions**
ile aynı mekanizmayı kullanır; farkı, veriyi kaydedebilmesi, koşullu
yakalayabilmesi ve dışarı aktarabilmesidir.

- Tarayıcı arayüzü: canlı grafikler, değişken arama, kayıt listesi, günlük
- Menülü terminal sürümü
- Boru hattı (`--stream`) ve UDP çıkışı: veriyi başka programa aktar
- `.elf` içindeki DWARF bilgisinden **gerçek tip** çözümü (`float` artık `u32` sanılmaz),
  yapı üyeleri (`imu.accel.x`) ve dizi elemanları (`buf[3]`)
- Osiloskop mantığında **tetikleyici**: `hata > 5` olunca öncesiyle birlikte kaydet
- Karta canlı **değer yazma** (PID katsayısı, eşik…)
- Kayıtları gerçek zamanlamasıyla **geri oynatma**
- Bağlantı koparsa otomatik yeniden bağlanma
- **Harici Python paketi yok**, yalnızca standart kütüphane

---

## Gereken

- **Python 3.11+**
- **STM32CubeIDE** ya da **STM32CubeProgrammer** — araç, bunlarla gelen
  `ST-LINK_gdbserver` ve `STM32_Programmer_CLI` programlarını kullanır ve
  bilinen kurulum yerlerinde kendisi bulur.

Araçlar alışılmadık bir yerdeyse ortam değişkeniyle gösterilir:

```bash
set SWD_GDBSERVER=C:/ST/.../ST-LINK_gdbserver.exe
set SWD_CUBEPROG=C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin
```

Windows'ta geliştirilip denendi.

---

## Başlarken

**En kolayı:** `Arayuzu-Ac.bat` dosyasına çift tıkla. Tarayıcıda
`http://127.0.0.1:8730` açılır.

Komut satırından:

```bash
python swdscope.py --web
```

```bash
python swdscope.py
```

Tarayıcıda ya da menüde üç adım:

1. Proje `.elf` dosyanı seç (CubeIDE projesinin `Debug/` klasöründe)
2. İzlemek istediğin değişkenleri işaretle
3. **Canlı veriyi çek**

Kart otomatik algılanır. Seçimlerin `swdscope_profil.json` dosyasına, kayıtlar
`logs/` klasörüne yazılır; ikisi de çalışma klasöründe oluşur.

---

## Kendi uygulamana bağlamak

```bash
python swdscope.py --stream | python benim_uygulamam.py
```

```python
import json, sys

for satir in sys.stdin:
    mesaj = json.loads(satir)
    if mesaj["type"] == "sample":
        print(mesaj["values"])
```

UDP ile (arayüz açıkken de çalışır):

```bash
python swdscope.py --web --udp 51999
```

Tam örnek: [`examples/kopru_ornegi.py`](examples/kopru_ornegi.py)

---

## Dosyalar

| Dosya | İşi |
|---|---|
| `swdscope.py` | Giriş noktası: menüler, komut satırı |
| `swdscope_core.py` | Kart algılama, profil, örnekleme, tetikleyici, çıkış biçimleri |
| `swd_link.py` | SWD katmanı: gdbserver + GDB uzak protokolü, ELF sembol okuma |
| `dwarf.py` | `.elf` hata ayıklama bilgisinden değişken tiplerini çözer |
| `web_server.py` | Tarayıcı arayüzünün sunucusu (HTTP + SSE) |
| `web/index.html` | Arayüzün kendisi; tek dosya, harici bağımlılık yok |
| `examples/` | Veriyi başka programa aktarma örneği |
| `tests/` | Kart takılı olmadan çalışan testler |
| `KILAVUZ.md` | Ayrıntılı kılavuz: kayıt biçimi, tetikleyici, hız, sorun giderme |

---

## Testler

```bash
python -m pip install pytest
```

```bash
python -m pytest -q
```

Sahte bir hedef belleği kullanılır; kart gerekmez. DWARF testleri
`arm-none-eabi-gcc` bulunamazsa atlanır.

---

## Bilinmesi gereken tek şey

**ST-LINK aynı anda tek istemci kabul eder.** CubeIDE'de debug oturumu açıkken
ya da CubeMonitor çalışırken bu araç karta bağlanamaz; biri kapanmalı.
