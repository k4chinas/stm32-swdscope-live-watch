# SWDScope — Kılavuz

Karttaki değişkenleri ST-LINK üzerinden canlı okuyup ekrana basan, dosyaya
kaydeden ve isteyene UDP ile yollayan araç. Menülü terminal sürümü ve
tarayıcı arayüzü aynı profili paylaşır.

Dönüştürücü yok, UART yok, kartta güç kablosu dışında kablo yok. CubeIDE'nin
**Live Expressions** penceresiyle aynı mekanizma; farkı, veriyi dosyaya ve
kendi uygulamana akıtabilmesi.

```bash
python swdscope.py
```

---

## İçindekiler

1. [Kurulum](#kurulum)
2. [Menülü kullanım](#menülü-kullanım)
3. [Arayüzsüz kullanım](#arayüzsüz-kullanım)
4. [Kayıt biçimi](#kayıt-biçimi)
5. [Kendi uygulamana bağlamak](#kendi-uygulamana-bağlamak)
6. [Profil dosyası](#profil-dosyası)
7. [Sık karşılaşılan sorunlar](#sık-karşılaşılan-sorunlar)

---

## Kurulum

**Yok.** Python 3.11+ ve CubeIDE (ya da CubeProgrammer) yeterli. Harici
paket kurulmaz; program bunlarla gelen `ST-LINK_gdbserver` ve
`STM32_Programmer_CLI` araçlarını kullanır. Araçlar şu sırayla aranır: ortam
değişkeni, STM32CubeProgrammer'ın varsayılan kurulum yeri, CubeIDE'nin
eklenti klasörü, `PATH`.

Araçlar farklı bir yerdeyse ortam değişkeniyle bildirilir:

```bash
set SWD_GDBSERVER=C:/ST/.../ST-LINK_gdbserver.exe
set SWD_CUBEPROG=C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin
```

### CubeIDE ile birlikte

Bu araç kendi GDB sunucusunu **61240** portunda açar. CubeIDE 61234 (SWV
için 61235) kullanır; portlar bilerek ayrı tutuldu ki biri diğerinin debug
oturumunu engellemesin. Gerekirse `SWD_GDB_PORT` ile değiştirilebilir.

Portlar ayrı olsa da **ST-LINK'in kendisi tek istemci kabul eder**:
CubeIDE'de debug oturumu açıkken bu araç bağlanamaz, tersi de geçerli.
Biri kapanmalı.

---

## Menülü kullanım

```bash
python swdscope.py
```

```
  durum : ● STM32F405xx/F407xx/F415xx/F417xx  SN 0123456789AB  2.95V
  proje : proje.elf
  izlem : g_mon_tick, g_mon_sine
  hız   : 50 Hz
----------------------------------------------------------
  [1] Bağlan / kartı tanı
  [2] Proje dosyası seç (.elf)
  [3] Değişken seç
  [4] Canlı veriyi çek
  [5] Ayarlar (hız, klasör)
  [6] Tetikleyici (koşullu kayıt)
  [7] Profili kaydet
  [0] Çıkış
```

**Kart otomatik algılanır.** Arka planda iki saniyede bir bakılır; USB'yi takıp
çıkardığında üstteki durum satırı kendiliğinden değişir. `[1]` yalnızca yonga
adını ve besleme gerilimini okumak için; bağlantı zaten kuruluyor.

**`[2] Proje dosyası`** yakındaki `Debug/` ve `Release/` klasörlerinde `.elf`
arar, en yeni derleme en üstte listelenir. Bulamazsa yolu elle yazarsın.

**`[3] Değişken seç`** `.elf` içindeki RAM değişkenlerini listeler:

- numara yazarak ekle/çıkar (seçili olanlar `*` ile işaretli)
- `/kelime` ile süz — uzun listede aradığını bulmanın yolu
- `t` ile bir değişkenin tipini değiştir
- Enter ile bitir

Tip, sembolün boyutuna bakılarak tahmin edilir (4 bayt → `u32`). Değişkenin
`float` olduğunu program bilemez; `t` ile `f32` seç. Desteklenen tipler:
`u8 i8 u16 i16 u32 i32 f32 f64`.

**`[4] Canlı veriyi çek`** akışı başlatır. Ekranda tablo yerinde güncellenir,
aynı anda `logs/` klasörüne hem NDJSON hem CSV yazılır. Ctrl+C ile durur.

Çıkarken profil kendiliğinden kaydedilir; ikinci açılışta seçimlerin yerinde.

---

## Tarayıcı arayüzü

```bash
python swdscope.py --web
```

Varsayılan tarayıcıda `http://127.0.0.1:8730` açılır. Sunucu **yalnızca
127.0.0.1'e** bağlanır; sayfa dışarıya açılmaz. Port değiştirmek için
`--web 9000`.

Terminal sürümüyle aynı profili kullanır: birinde yaptığın seçim diğerinde
de geçerlidir.

Sol sütunda `.elf` seçimi, aranabilir değişken listesi (tip açılır menüsüyle),
örnekleme hızı ve tetikleyici var. Sağda canlı sayaçlar, her değişken için
kendi ölçekli mini grafiği, kayıt listesi ve renk kodlu günlük bulunur —
yeşil başarı, kırmızı hata, sarı uyarı.

**Kart otomatik algılanır.** Arka planda üç saniyede bir bakılır; USB'yi takıp
çıkardığında rozet kendiliğinden değişir ve günlüğe düşer. Akış sırasında
yoklama durur, çünkü probe'u akışın kendisi tutar.

**Kart yokken "Canlı veriyi çek" basılamaz.** Değişken seçilmediğinde de öyle;
butonun üstüne gelince sebebi yazar.

**Değişken kümesi akış sürerken değiştirilebilir.** Yeni bir değişken
işaretlediğinde verisi hemen gelmeye başlar; bağlantı kopmaz, yalnızca
adresler yeniden çözülür.

**Oturum başına tek dosya çifti.** Kaç kez ayar değiştirirsen değiştir, akış
boyunca tek bir `.ndjson` yazılır; şema değişikliği araya `vars` satırı olarak
düşer. `.csv` ise akış bitince ondan üretilir ve sütunları oturum boyunca
izlenen **tüm** değişkenlerin birleşimi olur — bir değişken o an izlenmiyorsa
hücresi boş kalır.

**Tip yanlışsa ölçü kartı uyarır.** Sembol tablosunda tip bilgisi olmadığı için
4 baytlık her değişken `u32` varsayılır. Değer anlamsız büyükse kart altında
*"f32 olsaydı 0.6374 — tip yanlış olabilir"* yazar; açılır menüden düzeltirsin.

**Son 5 `.elf` hatırlanır.** Yol kutusuna tıklayınca son kullandığın
dosyalar açılır; birine tıklayınca seçilir. Listedeki **×** kaydedilenleri
siler (seçili dosyaya dokunmaz).

**Başka `.elf` seçtiğinde** eski seçimlerin o dosyada yoksa listenin başında
kırmızı, üstü çizili olarak görünürler ve kutucuklarından kaldırılabilirler;
"Hepsini kaldır" ile topluca gider. O sırada akış başlatılamaz — çünkü o
değişkenlerin adresi yoktur.

**Tip artık otomatik.** `.elf` içindeki hata ayıklama bilgisi (DWARF)
okunarak her değişkenin gerçek tipi çıkarılır — `float` bir değişken artık
`u32` sanılmaz. Değişken listesinde tipi turkuaz rozetli olanlar DWARF'tan,
gri olanlar boyuta göre tahmindir. Yapı üyeleri `imu.accel.x`, dizi elemanları
`buf[3]` diye ayrı ayrı seçilebilir.

**Karta değer yazma.** Ölçü kartındaki kalem düğmesi bir kutu açar; değeri
yazıp Enter'a basınca karta işlenir. PID katsayısı, eşik, hedef hız gibi
şeyleri firmware'i yeniden yüklemeden denemek için. Yalnızca izlenen
değişkenlere yazılır.

**`.elf`'i karta yükleme.** Sol sütundaki "Bu .elf'i karta yükle" düğmesi
firmware'i karta yazar, doğrular ve kartı yeniden başlatır (CubeIDE'deki
"debug atma"nın karşılığı). Akış sürerken yapılamaz; onay ister.

**Grafikler büyütülebilir.** Her grafiğin sağ alt köşesindeki tutamaktan
yüksekliği değiştirilir, boyut hatırlanır. Başlıktaki ⧎ düğmesi kartı tam
genişliğe alır. Grafiğin üstünde gezerken imleç o andaki değeri ve zamanı
gösterir.

**Kayıt oynatma.** Kayıtlar listesindeki "oynat" düğmesi kaydı gerçek
zamanlamasıyla geri oynatır; karta bağlanmaz. Üst çubuktaki hız düğmeleri
(0.25x … 10x, max) **oynatma sürerken** değiştirilebilir, etkisi anında
görülür.

**Kayıt silme.** Her satırdaki "sil" o kaydı (ndjson + csv) siler, başlıktaki
"Tümünü sil" hepsini. Akış sürerken o anki kayıt silinemez.

**Kayıtlar** kartından geçmiş oturumlar indirilebilir — her satırda `ndjson` ve
`csv` bağlantısı vardır. Dosyalar `logs/` klasöründe durur.

**Günlük her şeyi yazar:** açılış durumu, başlat/durdur, ayar değişiklikleri,
kart takma/çıkarma, bağlantı kopması ve dönüşü, tetikleyici yakalamaları, ve
akış sürerken 15 saniyede bir ilerleme satırı.

Canlı veri **SSE** (Server-Sent Events) ile akar; WebSocket kütüphanesi
kurmaya gerek yoktur. Örnekler tarayıcıya tek tek değil ~50 ms'lik kümeler
hâlinde gider: 500 Hz'de saniyede 500 mesaj göndermek sekmeyi boğardı.

Akış sürerken kayıt yine `logs/` altına NDJSON ve CSV olarak yazılır.

Arayüz açıkken veriyi başka bir programa da aktarmak için:

```bash
python swdscope.py --web --udp 51999
```

---

## Arayüzsüz kullanım

Menü yok, NDJSON doğrudan `stdout`'a. Boru hattına sokmak için:

```bash
python swdscope.py --stream
```

| Seçenek | İşi |
|---|---|
| `--stream` | Menüsüz mod, NDJSON stdout'a |
| `--web [PORT]` | Tarayıcı arayüzünü aç (varsayılan 8730) |
| `--list` | `.elf` içindeki RAM değişkenlerini listele |
| `--elf YOL` | Profildeki `.elf`'i geçersiz kıl |
| `--hz 100` | Örnekleme hızı |
| `--udp 51999` | Satırları bu UDP portuna da yolla (`--web` ile de çalışır) |
| `--trigger 'hata > 5'` | Koşullu kayıt; boş dize kapatır |
| `--pre 200` | Tetik öncesi saklanan örnek sayısı |
| `--post 200` | Tetik sonrası örnek sayısı |
| `--count 500` | Bu kadar örnek alıp dur (0 = sonsuz) |

Değişken seçimi profilden okunur; önce bir kez menülü modda seçmek gerekir.

---

## Kayıt biçimi

**NDJSON** — satır başına bir JSON nesnesi. Üç tür satır var, hepsinde `type`
alanı bulunur.

```json
{"type":"header","format":1,"started":"2026-09-10T14:34:39","hz":50.0,"elf":"...","vars":[{"name":"sayac","type":"u32","addr":"0x200000CC"}]}
{"type":"sample","seq":0,"t":0.000451,"values":{"sayac":1883,"sinus":0.5090814}}
{"type":"sample","seq":1,"t":0.020867,"values":{"sayac":1883,"sinus":0.5062}}
{"type":"vars","vars":[{"name":"a","type":"u32","addr":"0x20000000"}]}
{"type":"link","event":"koptu","detail":"...","t":12.4}
{"type":"link","event":"baglandi","detail":"1. denemede baglanildi","t":16.9}
{"type":"trigger","event":"tetiklendi","capture":1,"expr":"hata > 5","t":1.35}
{"type":"end","samples":9,"sampled":300,"seconds":15.0,"reason":"bitti"}
```

`vars` satırı izlenen değişken kümesinin değiştiğini, `link` satırları bağlantının koptuğu ve geri geldiği anı işaretler; kayda
bakarken verideki boşluğun sebebini anlamanı sağlar. Tanımadığın bir `type`
görürsen atla — ileride yenileri eklenebilir.

| Alan | Anlamı |
|---|---|
| `seq` | Örnek sırası, sıfırdan başlar; atlama varsa veri kaybı olmuştur |
| `t` | Akışın başından itibaren geçen saniye (float) |
| `values` | Değişken adı → değer. İhtiyacın olan tek alan bu |
| `reason` | Akış neden bitti: `bitti`, `kullanıcı durdurdu` ya da hata metni |
| `samples` | Kayda **yazılan** örnek sayısı |
| `sampled` | Karttan **okunan** örnek sayısı (tetikleyici varken çok daha büyük) |

Neden NDJSON: her satır tek başına geçerli bir JSON. Akışın ortasından
başlasan bile o satırı çözebilirsin, dosyanın tamamını belleğe almak
gerekmez, `tail -f` ile izlenebilir ve her dilde üç satır kodla okunur.

Aynı veri yanında `.csv` olarak da yazılır (`t,seq,degisken1,degisken2`) —
Excel'e atmak istersen.

---

## Kendi uygulamana bağlamak

Tam çalışan örnek: [`examples/kopru_ornegi.py`](examples/kopru_ornegi.py)

**Boru hattı** — en basiti:

```bash
python swdscope.py --stream | python benim_uygulamam.py
```

```python
import json, sys

for satir in sys.stdin:
    mesaj = json.loads(satir)
    if mesaj["type"] == "sample":
        print(mesaj["values"])        # {"sayac": 1883, "sinus": 0.509}
```

**UDP** — uygulaman ayrı çalışsın istiyorsan:

```bash
python swdscope.py --stream --udp 51999
```

```python
import json, socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", 51999))

while True:
    veri, _ = sock.recvfrom(65535)
    mesaj = json.loads(veri)
    if mesaj["type"] == "sample":
        print(mesaj["values"])
```

**Kayıtlı dosyadan** — sonradan çözümlemek için:

```python
import json

with open("logs/20260910-143439.ndjson", encoding="utf-8") as f:
    ornekler = [json.loads(s) for s in f if '"sample"' in s]
```

---

## Profil dosyası

Seçimler `swdscope_profil.json` içinde tutulur, çıkışta kendiliğinden yazılır:

```json
{
  "elf": "C:/proje/Debug/proje.elf",
  "hz": 50.0,
  "out_dir": "logs",
  "variables": [
    {"name": "g_mon_tick", "type": "u32", "addr": 0},
    {"name": "g_mon_sine", "type": "f32", "addr": 0}
  ]
}
```

`.elf` verilmişse `addr` alanı önemsizdir; adresler **her akış başında**
sembol adından yeniden çözülür. Firmware'i yeniden derleyip yüklediğinde
kayan adresler böylece kendiliğinden düzelir.

`.elf` kullanmıyorsan `addr` alanına adresi elle yazabilirsin.

---

## Sık karşılaşılan sorunlar

**"Kart bulunamadi - ST-LINK takili degil"** — USB kablosu takılı değil.

**"ST-LINK gorunuyor ama baglanilamadi"** — kart takılı ama başkası kullanıyor:
CubeIDE'de bir debug oturumu ya da CubeMonitor açıktır. **Probe tek istemci
kabul eder**; biri kapanmadan diğeri bağlanamaz.

**CubeIDE'de derleme/debug yaparken `.elf` tanınmıyor** — derleme sırasında
dosya bir an yarım ya da yok olur. Program bunu 0,4 saniye arayla üç kez
yeniden dener; yine olmazsa *"dosya şu anda yazılıyor olabilir"* der.
Derleme bitince listeyi yenilemen yeter. **Not:** CubeIDE'de debug oturumu
açıkken bu araç karta bağlanamaz — probe tek istemci kabul eder; ama `.elf`
okumak için karta gerek yoktur, değişken seçmeye devam edebilirsin.

**CubeIDE "Failed to start GDB server" diyor** — CubeIDE'nin portunu (61234)
başka bir süreç tutuyordur. Bu araç 61240 kullandığı için normalde çakışmaz;
yine de takılı kalmış bir sunucu varsa Görev Yöneticisi'nden
`ST-LINK_gdbserver.exe` süreçlerini kapat.

**Akış ortasında bağlantı koparsa** program iki saniyede bir yeniden
bağlanmayı dener. **En fazla 10 deneme, yaklaşık 20 saniye** — sonra pes eder
ve akış "vazgecildi" sebebiyle biter. Ctrl+C ile daha erken de kesebilirsin.

Bağlantı geri geldiğinde akış kaldığı yerden sürer: `seq` sayacı atlamaz,
`t` sıfırlanmaz, kopukluk kayıtta `link` satırlarıyla işaretlenir. Böylece
sonradan log'a bakınca verideki boşluğun nerede ve neden olduğu bellidir.

Deneme bütçesi ancak **gerçekten veri aktığında** yenilenir. Bağlantı kurulup
ilk okumada tekrar kopuyorsa (kart yarı ölü, kablo temassız) program sonsuza
kadar dönmez, bütçe tükenir ve durur.

**Kart çıkarıldığında** akış bunu en geç iki saniyede fark eder: veri
okumaları hata vermese bile hattın canlılığı düzenli olarak CPUID yazmacı
okunarak sınanır. Kopma günlüğe kırmızı düşer, rozet söner, yeniden bağlanma
başlar.

**Değerler donuk görünüyor** — önce pencereye bak: 50 Hz'de `--count 12`
sadece 0,24 saniyedir, saniyede birkaç kez değişen bir sayaç o aralıkta
kıpırdamaz. Gerçekten donuksa çekirdek durmuştur; program bağlanırken
`QNonStop:1` + `vCont;c` ile devam ettirir, bu başarısız olursa akış başlar
ama değerler sabit kalır.

**Değer saçma çıkıyor** (`5.4e28` gibi) — tip yanlış. Normalde DWARF bunu
kendiliğinden çözer; çözemediyse (hata ayıklama bilgisi olmayan bir birim)
tipi elle seç: web arayüzünde açılır menüden, terminalde `[3]` → `t`.

**Sembol bulunamadı** — `.elf` karttaki firmware ile aynı derleme değil.
Yeniden yükle ya da `.elf`'i güncelle.

**Örnekleme hızı istediğinden düşük** — aşağıdaki tavan bölümüne bak.

---

## Hız tavanı

Darboğaz SWD hattı değil, **USB gidiş-dönüş gecikmesi**. Her okuma yaklaşık
0,4 ms sürüyor; bu yüzden okunan bayt sayısı arttıkça hız düşer ama SWD saat
frekansını yükseltmek hiçbir şey değiştirmez.

STM32F407 + ST-LINK/V2 üzerinde ölçülen değerler:

| Tek okumadaki bayt | Okuma/saniye | Okuma başına |
|---|---|---|
| 4 (tek değişken) | ~2450 | 0,41 ms |
| 8 (iki değişken) | ~2200 | 0,45 ms |
| 64 | ~1220 | 0,82 ms |
| 256 | ~490 | 2,03 ms |
| 1024 | ~140 | 7,26 ms |

gdbserver'a `--frequency 24000` vermek ölçülebilir bir fark yaratmadı: 4 MHz
de 24 MHz de aynı sonucu verdi. Yani hızlanmak istiyorsan yapılacak şey SWD
saatini kurcalamak değil, **daha az bayt okumak**.

İstenen hıza ne kadar yaklaşılıyor (iki `u32` değişkenle):

| İstenen | Gerçekleşen |
|---|---|
| 50 Hz | 50 Hz |
| 500 Hz | 499 Hz |
| 1000 Hz | 976 Hz |
| `--hz 0` (serbest koşu) | ~2000 Hz |

1000 Hz'e kadar zamanlama neredeyse birebir tutuyor. Üstünde istersen `--hz 0`
ile frenleri tamamen bırakabilirsin, ama o zaman örnekler eşit aralıklı olmaz.

**Daha hızlı örneklemek her zaman daha çok bilgi demek değil.** Firmware'in o
değişkeni saniyede 100 kez güncelliyorsa 1000 Hz'de okumak aynı değeri on kez
kaydeder. Kabaca güncelleme hızının iki katını seç, gerisi yer kaplar.

Değişkenleri RAM'de birbirine yakın tutmak işe yarar: 512 bayta sığan aralık
tek okumada alınır, sığmayanlar tek tek okunur ve her biri ayrı bir gidiş-dönüş
maliyeti getirir.

---

## Tetikleyici — koşullu kayıt

Osiloskop mantığı: bir koşul sağlandığı anda, **öncesindeki** örneklerle
birlikte kaydet. Saatte bir olan bir arızayı yakalamanın pratik yolu budur —
saatlerce her şeyi kaydedip içinde aramak yerine, sadece olayın çevresini
saklarsın.

Menüden `[6]`, ya da komut satırından:

```bash
python swdscope.py --stream --trigger "hata > 5" --pre 200 --post 200
```

### Nasıl çalışır

Program `pre` kadar örneği sürekli bir halka tamponda bekletir. Koşul
**yükselen kenarda** — yanlıştan doğruya geçtiği anda — tetiklenir; o an
tamponun tamamı, tetikleyen örnek ve sonraki `post` örnek kayda yazılır.
Bir yakalama toplam `pre + 1 + post` örnek tutar.

Koşul doğru kaldığı sürece yeniden tetiklenmez; yoksa her örnekte yeni bir
yakalama başlardı. Koşul düşüp tekrar yükseldiğinde yeniden kurulur.

`seq` numaraları korunur, sıfırlanmaz. Kayıtta iki yakalama arasında numara
atlaması görürsün — aradaki örnekler okundu ama koşul sağlanmadığı için
yazılmadı.

### Koşul nasıl yazılır

Değişken adlarını doğrudan kullanırsın:

```
hata > 5
abs(fark) > 0.5 and hiz < 10
sicaklik > 80 or fan_pwm == 0
max(a, b) - min(a, b) > 100
```

Karşılaştırma (`> < >= <= == !=`), mantık (`and or not`), aritmetik
(`+ - * / % **`) ve dört fonksiyon (`abs min max round`) kullanılabilir.

Koşul **en az bir değişken kullanmalı** ve bir **karşılaştırma** olmalı. `3`
ya da `hata` gibi ifadeler reddedilir: ikisi de sabit/doğru olduğu için bir kez
tetikler, sonra asla — kayıt bir saniyelik kalır ve sebebi anlaşılmaz.

Koşul akışın ilk örneğinde zaten doğruysa tetiklenmez; gerçek bir geçiş
(yanlıştan doğruya) beklenir. Osiloskop da önce kurulur, sonra geçişi bekler.

Bunun için karta kod yazmıyorsun, derleyici de gerekmiyor: koşul PC'de,
çekilmiş veri üzerinde değerlendiriliyor. İfade `ast` ile ayrıştırılıp
yalnızca izin verilen yapılara geçit veriliyor — nitelik erişimi,
indeksleme, fonksiyon tanımı gibi şeyler reddediliyor:

```
hata.__class__     ->  ifadede kullanilamaz: Attribute
open("x")          ->  yalnizca su fonksiyonlar kullanilabilir: abs, max, min, round
yok > 1            ->  bilinmeyen ad: yok (secili degiskenler: hata, hiz)
```

### Kayıtta ne görünür

```json
{"type":"trigger","event":"tetiklendi","capture":1,"expr":"hata > 5","t":1.35}
{"type":"sample","seq":22,...}
{"type":"trigger","event":"tamamlandi","capture":1,"t":1.60}
```

`tetiklendi` ve `tamamlandi` satırları yakalamaların sınırlarını işaretler;
kaydı okurken hangi örneklerin hangi olaya ait olduğu bellidir.

### Ayarlar

| Ayar | Anlamı |
|---|---|
| Koşul | Boş bırakırsan tetikleyici kapanır, her şey kaydedilir |
| `pre` | Tetikten önce saklanan örnek (bellekte tutulur) |
| `post` | Tetikten sonra yazılan örnek |
| Yakalama sınırı | 0 = sınırsız; verilen sayıya ulaşınca akış kendiliğinden biter |
