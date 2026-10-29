"""Configuration; importing this module never exposes credentials."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')


@dataclass(frozen=True)
class Settings:
    token: str = os.getenv('BOT_TOKEN', '')
    admin_ids: frozenset[int] = frozenset(
        int(value.strip()) for value in os.getenv('ADMIN_IDS', '').split(',')
        if value.strip().isdigit()
    )
    shop_name: str = 'Coffee Time Almaty'
    address: str = 'проспект Абая, 00 (демонстрационный адрес)'
    db_path: Path = ROOT / 'coffee.db'


settings = Settings()
