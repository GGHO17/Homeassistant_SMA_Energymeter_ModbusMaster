"""Geraeteprofile.

Ein Profil beschreibt, wie ein Geraet gelesen wird - Registerkarte fuer
Modbus oder Topic-Zuordnung fuer MQTT. Profile sind YAML-Dateien und liegen in

    custom_components/sma_meter_sim/device_profiles/   (mitgeliefert)
    <config>/sma_meter_sim_profiles/                   (eigene, updatefest)

Damit laesst sich ein neues Geraet ohne Code-Aenderung ergaenzen: Datei
ablegen, Integration neu laden, Profil im Dialog auswaehlen.

Erlaubte Messgroessen (key):
    p, q, s, current, voltage, cos_phi, frequency
Phase: l1 | l2 | l3 | leer (= Summenwert)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .sources.modbus import RegisterDef
from .sources.mqtt import TopicDef

_LOGGER = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).parent / "device_profiles"
USER_DIR_NAME = "sma_meter_sim_profiles"

PROFILE_CUSTOM = "custom"


@dataclass
class DeviceProfile:
    key: str
    name: str
    protocol: str = "modbus"  # modbus | mqtt
    default_interval_ms: int = 100
    word_swap: bool = False
    registers: list[RegisterDef] = field(default_factory=list)
    topics: list[TopicDef] = field(default_factory=list)
    notes: str = ""

    @staticmethod
    def from_dict(key: str, data: dict) -> "DeviceProfile":
        return DeviceProfile(
            key=key,
            name=data.get("name", key),
            protocol=data.get("protocol", "modbus"),
            default_interval_ms=int(data.get("default_interval_ms", 100)),
            word_swap=bool(data.get("word_swap", False)),
            notes=data.get("notes", ""),
            registers=[
                RegisterDef(
                    key=r["key"],
                    address=int(r["address"]),
                    dtype=r.get("dtype", "float32"),
                    scale=float(r.get("scale", 1.0)),
                    phase=r.get("phase") or None,
                    input_register=bool(r.get("input_register", True)),
                )
                for r in data.get("registers", [])
            ],
            topics=[
                TopicDef(
                    topic=t["topic"],
                    key=t["key"],
                    phase=t.get("phase") or None,
                    scale=float(t.get("scale", 1.0)),
                    value_path=t.get("value_path"),
                )
                for t in data.get("topics", [])
            ],
        )


def _load_dir(directory: Path) -> dict[str, DeviceProfile]:
    profiles: dict[str, DeviceProfile] = {}
    if not directory.is_dir():
        return profiles
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue  # Vorlagen und Notizen ueberspringen
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            profiles[path.stem] = DeviceProfile.from_dict(path.stem, data)
        except (OSError, yaml.YAMLError, KeyError, ValueError) as err:
            _LOGGER.error("Profil %s konnte nicht gelesen werden: %s", path.name, err)
    return profiles


def load_profiles(user_dir: str | None = None) -> dict[str, DeviceProfile]:
    """Mitgelieferte und eigene Profile laden (eigene gewinnen bei Namensgleichheit).

    Blockierende Datei-I/O - im Event-Loop bitte ueber async_add_executor_job.
    """
    profiles = _load_dir(BUILTIN_DIR)
    if user_dir:
        profiles.update(_load_dir(Path(user_dir)))
    return profiles


def profile_labels(profiles: dict[str, DeviceProfile], protocol: str) -> dict[str, str]:
    """Auswahlliste fuer den Dialog, gefiltert nach Protokoll."""
    labels = {
        key: prof.name
        for key, prof in sorted(profiles.items())
        if prof.protocol == protocol
    }
    labels[PROFILE_CUSTOM] = "Eigene Zuordnung (manuell)"
    return labels
