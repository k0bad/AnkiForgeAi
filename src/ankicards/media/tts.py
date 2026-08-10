"""TTS через edge-tts (Microsoft Edge нейросетевые голоса, бесплатно).

Голоса bokmål:
- nb-NO-FinnNeural    (мужской)
- nb-NO-PernilleNeural (женский)

Имя файла детерминировано: {card.id}_nb.mp3
Сохраняется в media/audio/, в БД хранится только имя.
"""

from __future__ import annotations

from pathlib import Path

import edge_tts

from ..config import Config
from ..models import POS, Card


def _pronounceable_text(card: Card) -> str:
    """Убрать инфинитивную частицу å у глаголов для более естественного произношения."""
    text = card.word.strip()
    if card.pos == POS.VERB and text.lower().startswith("å "):
        return text[2:]
    return text


def _voice_for(cfg: Config) -> str:
    if cfg.tts.default_voice.lower() == "male":
        return cfg.tts.voice_male
    return cfg.tts.voice_female


async def generate_audio(card: Card, cfg: Config) -> Card:
    """Сгенерировать .mp3 для card.word, обновить card.audio."""
    filename = f"{card.id}_nb.mp3"
    out_path = cfg.paths.audio_dir / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text = _pronounceable_text(card)
    await _synthesize(
        text=text,
        voice=_voice_for(cfg),
        out_path=out_path,
        rate=cfg.tts.rate,
        pitch=cfg.tts.pitch,
    )
    card.audio = filename
    return card


async def _synthesize(text: str, voice: str, out_path: Path, rate: str, pitch: str) -> None:
    """Низкоуровневая обёртка над edge_tts.Communicate."""
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(out_path))
