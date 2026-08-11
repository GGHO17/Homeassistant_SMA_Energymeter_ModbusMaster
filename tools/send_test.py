#!/usr/bin/env python3
"""Standalone-Testsender - laeuft ohne Home Assistant.

Sendet ein simuliertes Lastprofil als Energy-Meter-Telegramm, damit sich der
Encoder mit Wireshark pruefen laesst:

    tshark -i eth0 -f "udp port 9522" -x

Aufruf:
    python3 tools/send_test.py --serial 1900000001 --interval 1.0
    python3 tools/send_test.py --dump-only        # nur Hexdump, kein Versand
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from pathlib import Path

# Paket-Stub: laedt nur die HA-freien Module, ohne custom_components/
# sma_meter_sim/__init__.py auszufuehren (das wuerde Home Assistant brauchen).
import types  # noqa: E402

_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "sma_meter_sim"
_pkg = types.ModuleType("sma_meter_sim")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules["sma_meter_sim"] = _pkg

from sma_meter_sim.pipeline import MeterPipeline  # noqa: E402
from sma_meter_sim.speedwire import (  # noqa: E402
    SUSYID_ENERGY_METER_20,
    SUSYID_HOME_MANAGER_20,
    SpeedwireSender,
    TelegramBuilder,
)


def simulate(pipeline: MeterPipeline, t: float) -> None:
    """Sinusfoermiges Lastprofil zwischen -3000 und +5000 W."""
    total = 1000 + 4000 * math.sin(t / 10)
    for i, phase in enumerate(("l1", "l2", "l3")):
        p = total / 3 + 200 * math.sin(t / 3 + i)
        pipeline.feed("p", p, phase)
        pipeline.feed("q", p * 0.1, phase)
        pipeline.feed("s", abs(p) * 1.02, phase)
        pipeline.feed("current", abs(p) / 230, phase)
        pipeline.feed("voltage", 230 + math.sin(t + i), phase)
    pipeline.feed("p", total)
    pipeline.feed("q", total * 0.1)
    pipeline.feed("s", abs(total) * 1.02)
    pipeline.feed("cos_phi", 0.98)
    pipeline.feed("frequency", 50.01)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", type=int, default=1900000001)
    ap.add_argument("--susy", choices=["em", "shm"], default="em")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--interface", default=None, help="IP des sendenden Interface")
    ap.add_argument("--dump-only", action="store_true")
    args = ap.parse_args()

    susy = SUSYID_ENERGY_METER_20 if args.susy == "em" else SUSYID_HOME_MANAGER_20
    pipeline = MeterPipeline(smoothing_s=0.1)
    simulate(pipeline, 0.0)

    if args.dump_only:
        frame = TelegramBuilder(susy, args.serial).build(pipeline.snapshot())
        print(f"Laenge: {len(frame)} Byte")
        for offset in range(0, len(frame), 16):
            chunk = frame[offset : offset + 16]
            print(f"{offset:04x}  " + " ".join(f"{b:02x}" for b in chunk))
        return

    sender = SpeedwireSender(susy, args.serial, interface_ip=args.interface)
    await sender.async_start()
    t0 = time.monotonic()
    try:
        while True:
            t = time.monotonic() - t0
            simulate(pipeline, t)
            sender.send(pipeline.snapshot())
            print(
                f"\rt={t:7.1f}s  P={pipeline.get('p'):8.1f} W  "
                f"gesendet={sender.sent_count}",
                end="",
            )
            await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        await sender.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
