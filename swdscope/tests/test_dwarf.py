from __future__ import annotations

import glob
import subprocess

import pytest

import dwarf

GCC_DESEN = ("C:/ST/STM32CubeIDE_*/STM32CubeIDE/plugins/"
             "com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32*/"
             "tools/bin/arm-none-eabi-gcc.exe")

KAYNAK = """
#include <stdint.h>

typedef struct { float x; float y; int16_t z; } Vec;
typedef struct { Vec accel; uint8_t hazir; } Imu;

volatile float          g_sinus   = 0.5f;
volatile uint32_t       g_sayac   = 7;
volatile int16_t        g_hiz     = -300;
volatile uint8_t        g_bayrak  = 1;
volatile double         g_cift    = 1.5;
volatile Imu            g_imu;
volatile uint16_t       g_dizi[4];
volatile uint32_t      *g_isaretci;

int main(void) {
    g_sayac = 8; g_sinus = 1.0f; g_hiz = -1; g_bayrak = 0;
    g_cift = 2.5; g_imu.hazir = 1; g_dizi[2] = 9; g_isaretci = 0;
    return 0;
}
"""

BAGLAYICI = """
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 512K
         RAM  (rwx): ORIGIN = 0x20000000, LENGTH = 128K }
SECTIONS { .text : { *(.text*) *(.rodata*) } > FLASH
           .data : { *(.data*) } > RAM AT> FLASH
           .bss  : { *(.bss*) *(COMMON) } > RAM }
"""


def _gcc():
    bulunan = glob.glob(GCC_DESEN)
    return bulunan[0] if bulunan else None


@pytest.fixture(scope="module")
def test_elf(tmp_path_factory):
    gcc = _gcc()
    if not gcc:
        pytest.skip("arm-none-eabi-gcc bulunamadı")
    d = tmp_path_factory.mktemp("dwarf")
    c, ld, elf = d / "t.c", d / "t.ld", d / "t.elf"
    c.write_text(KAYNAK, encoding="utf-8")
    ld.write_text(BAGLAYICI, encoding="utf-8")
    sonuc = subprocess.run(
        [gcc, "-mcpu=cortex-m4", "-mthumb", "-O0", "-g3", "-nostdlib",
         "-nostartfiles", "-T", str(ld), "-o", str(elf), str(c)],
        capture_output=True, text=True)
    if sonuc.returncode != 0 or not elf.exists():
        pytest.skip("test .elf derlenemedi: %s" % sonuc.stderr[:200])
    return str(elf)


def test_temel_tipler_cozulur(test_elf):
    t = dwarf.degisken_tipleri(test_elf)
    assert t["g_sinus"][1] == "f32"
    assert t["g_sayac"][1] == "u32"
    assert t["g_hiz"][1] == "i16"
    assert t["g_bayrak"][1] == "u8"
    assert t["g_cift"][1] == "f64"


def test_isaretci_u32_sayilir(test_elf):
    assert dwarf.degisken_tipleri(test_elf)["g_isaretci"][1] == "u32"


def test_yapi_uyeleri_acilir(test_elf):
    t = dwarf.degisken_tipleri(test_elf)
    assert t["g_imu.accel.x"][1] == "f32"
    assert t["g_imu.accel.z"][1] == "i16"
    assert t["g_imu.hazir"][1] == "u8"
    assert t["g_imu.accel.y"][0] - t["g_imu.accel.x"][0] == 4


def test_dizi_elemanlari_acilir(test_elf):
    t = dwarf.degisken_tipleri(test_elf)
    for i in range(4):
        assert t["g_dizi[%d]" % i][1] == "u16"
    assert t["g_dizi[1]"][0] - t["g_dizi[0]"][0] == 2


def test_adresler_sembol_tablosuyla_ayni(test_elf):
    from swd_link import elf_symbols
    semboller = elf_symbols(test_elf)
    t = dwarf.degisken_tipleri(test_elf)
    for ad in ("g_sinus", "g_sayac", "g_hiz"):
        assert t[ad][0] == semboller[ad][0]


def test_hata_ayiklama_bilgisi_yoksa_hata(tmp_path):
    sahte = tmp_path / "yok.elf"
    sahte.write_bytes(bytes([0x7F]) + b"ELF" + bytes(60))
    with pytest.raises(dwarf.DwarfHatasi):
        dwarf.degisken_tipleri(str(sahte))
