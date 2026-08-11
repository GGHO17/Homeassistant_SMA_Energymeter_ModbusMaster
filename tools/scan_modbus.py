#!/usr/bin/env python3
"""Modbus-Adressraum absuchen und plausible Messwerte anzeigen.

Hilft, wenn keine Registerkarte vorliegt: liest einen Adressbereich, deutet
ihn in mehreren Varianten (float32 in beiden Wortreihenfolgen, int32) und
zeigt nur Werte an, die als Leistung, Spannung, Strom oder Frequenz
plausibel sind.

    pip install pymodbus
    python3 tools/scan_modbus.py 192.168.1.50 --start 0 --end 2000
    python3 tools/scan_modbus.py 192.168.1.50 --start 1000 --end 1100 --holding
    python3 tools/scan_modbus.py 192.168.1.50 --watch 1013 --dtype float32

--watch beobachtet eine Adresse fortlaufend: schalte einen groesseren
Verbraucher ein und sieh zu, welcher Wert sich passend aendert.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import struct

# Plausibilitaetsfenster je Groesse (Untergrenze, Obergrenze)
BANDS = {
    "Spannung [V]": (180.0, 280.0),
    "Frequenz [Hz]": (45.0, 65.0),
    "Strom [A]": (0.05, 200.0),
    "Leistung [W/var/VA]": (20.0, 100000.0),
    "cos phi": (0.3, 1.0),
}


def classify(value: float) -> str | None:
    if value != value or abs(value) in (float("inf"),):  # NaN / inf
        return None
    magnitude = abs(value)
    hits = [name for name, (lo, hi) in BANDS.items() if lo <= magnitude <= hi]
    return ", ".join(hits) if hits else None


def decode_all(words: list[int], index: int) -> dict[str, float]:
    """Ein Registerpaar in mehreren Varianten deuten."""
    out: dict[str, float] = {}
    if index + 1 < len(words):
        hi, lo = words[index], words[index + 1]
        big = struct.pack(">HH", hi, lo)
        little = struct.pack(">HH", lo, hi)
        try:
            out["float32 (ABCD)"] = struct.unpack(">f", big)[0]
            out["float32 (CDAB)"] = struct.unpack(">f", little)[0]
            out["int32"] = float(struct.unpack(">i", big)[0])
        except struct.error:
            pass
    out["int16"] = float(struct.unpack(">h", struct.pack(">H", words[index]))[0])
    return out


def _unit_kwargs(func, unit: int) -> dict:
    params = inspect.signature(func).parameters
    for candidate in ("device_id", "slave", "unit"):
        if candidate in params:
            return {candidate: unit}
    return {}


async def read_range(client, start: int, count: int, holding: bool, unit: int):
    func = client.read_holding_registers if holding else client.read_input_registers
    result = await func(start, count=count, **_unit_kwargs(func, unit))
    if result.isError():
        return None
    return result.registers


async def scan(args) -> None:
    from pymodbus.client import AsyncModbusTcpClient

    client = AsyncModbusTcpClient(args.host, port=args.port)
    if not await client.connect():
        print(f"Keine Verbindung zu {args.host}:{args.port}")
        return

    space = "Holding" if args.holding else "Input"
    print(f"Scanne {space}-Register {args.start}..{args.end} auf {args.host}\n")

    found = 0
    address = args.start
    chunk = args.chunk
    while address < args.end:
        count = min(chunk, args.end - address)
        words = await read_range(client, address, count, args.holding, args.unit)
        if words is None:
            # Block nicht lesbar - halbieren, sonst weiterspringen
            if chunk > 2:
                chunk = max(2, chunk // 2)
                continue
            address += 2
            chunk = args.chunk
            continue

        for i in range(0, len(words) - 1):
            for label, value in decode_all(words, i).items():
                if label == "int16":
                    continue
                kind = classify(value)
                if kind:
                    print(
                        f"  {address + i:>6}  {label:<16} {value:>14.3f}   -> {kind}"
                    )
                    found += 1
        address += count
        chunk = args.chunk

    print(f"\n{found} plausible Werte. Register mit --watch gegenpruefen.")
    client.close()


async def watch(args) -> None:
    from pymodbus.client import AsyncModbusTcpClient

    client = AsyncModbusTcpClient(args.host, port=args.port)
    if not await client.connect():
        print(f"Keine Verbindung zu {args.host}:{args.port}")
        return

    print(f"Beobachte Adresse {args.watch} - Verbraucher schalten, Abbruch mit Strg+C\n")
    try:
        while True:
            words = await read_range(client, args.watch, 2, args.holding, args.unit)
            if words is None:
                print("  Lesefehler")
            else:
                parts = [f"{k}={v:.3f}" for k, v in decode_all(words, 0).items()]
                print("  " + "   ".join(parts))
            await asyncio.sleep(args.period)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--unit", type=int, default=1)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=2000)
    ap.add_argument("--chunk", type=int, default=100)
    ap.add_argument("--holding", action="store_true", help="Holding statt Input")
    ap.add_argument("--watch", type=int, default=None, help="eine Adresse beobachten")
    ap.add_argument("--period", type=float, default=1.0)
    args = ap.parse_args()

    asyncio.run(watch(args) if args.watch is not None else scan(args))


if __name__ == "__main__":
    main()
