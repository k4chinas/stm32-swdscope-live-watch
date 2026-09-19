from __future__ import annotations

import json
import sys


def veriyi_isle(values: dict[str, float | int], t: float) -> None:
    satir = "  ".join(f"{ad}={deger}" for ad, deger in values.items())
    print(f"t={t:7.3f}  {satir}")


def borudan_oku() -> None:
    for satir in sys.stdin:
        satir = satir.strip()
        if not satir:
            continue
        mesaj = json.loads(satir)

        if mesaj["type"] == "header":
            adlar = [v["name"] for v in mesaj["vars"]]
            print(f"Akış başladı: {', '.join(adlar)}  ({mesaj['hz']} Hz)")
        elif mesaj["type"] == "sample":
            veriyi_isle(mesaj["values"], mesaj["t"])
        elif mesaj["type"] == "end":
            print(f"Akış bitti: {mesaj['samples']} örnek, {mesaj['reason']}")


def udpden_oku(port: int) -> None:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    print(f"Dinleniyor: 127.0.0.1:{port}  (Ctrl+C ile çık)")

    try:
        while True:
            veri, _adres = sock.recvfrom(65535)
            mesaj = json.loads(veri.decode("utf-8"))
            if mesaj["type"] == "sample":
                veriyi_isle(mesaj["values"], mesaj["t"])
            elif mesaj["type"] == "end":
                print(f"Akış bitti: {mesaj['samples']} örnek")
    except KeyboardInterrupt:
        print("\nDurduruldu.")
    finally:
        sock.close()


if __name__ == "__main__":
    if "--udp" in sys.argv:
        udpden_oku(int(sys.argv[sys.argv.index("--udp") + 1]))
    else:
        borudan_oku()
