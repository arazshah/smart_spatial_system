from __future__ import annotations

from dataclasses import fields

from qgis.core import QgsSettings

from .config import ChatConfig

_PREFIX = "s3geo/chat/"


def load_config() -> ChatConfig:
    s = QgsSettings()
    base = ChatConfig()
    data = {}
    for f in fields(ChatConfig):
        if f.name == "extra":
            continue
        default = getattr(base, f.name)
        value = s.value(_PREFIX + f.name, default)
        if isinstance(default, bool):
            value = str(value).lower() in ("true", "1")
        elif isinstance(default, int):
            value = int(value)
        elif isinstance(default, float):
            value = float(value)
        data[f.name] = value
    return ChatConfig.from_dict(data)


def save_config(cfg: ChatConfig) -> None:
    s = QgsSettings()
    for k, v in cfg.to_dict().items():
        s.setValue(_PREFIX + k, v)


def notice_acknowledged() -> bool:
    return str(QgsSettings().value(_PREFIX + "notice_ack", "false")).lower() == "true"


def acknowledge_notice() -> None:
    QgsSettings().setValue(_PREFIX + "notice_ack", True)
