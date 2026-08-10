"""Создать пустую БД с нужной схемой.

Запуск: python scripts/init_db.py
Идемпотентно — повторный запуск ничего не ломает.
"""

from __future__ import annotations

from ankicards.config import get_config
from ankicards.db import Database


def main() -> None:
    cfg = get_config()
    Database(cfg.paths.db)
    print(f"✓ База создана: {cfg.paths.db}")


if __name__ == "__main__":
    main()
