import sys
sys.path.insert(0, '/opt/data/projects/Anki/src')
from ankicards.config import get_config
from ankicards.db import Database
from ankicards.models import Card, POS, Status
from ankicards.dedupe import check_card, _normalize

cfg = get_config()
print("auto threshold:", cfg.dedupe.fuzzy_threshold_auto, "review:", cfg.dedupe.fuzzy_threshold_review)
db = Database(cfg.paths.db)

card = Card(word="en bedrift", pos=POS.NOUN, translation="компания", topic="arbeid", source="topic-gen", status=Status.PENDING)
d = check_card(card, db, cfg)
print("Decision:", d.decision, "| reason:", d.reason)
for m in d.matches:
    print("  match:", repr(m.existing_word), "score", m.score)

import sqlite3
conn=sqlite3.connect('/opt/data/projects/Anki/data/ankicards.db')
conn.row_factory=sqlite3.Row
rows=conn.execute("SELECT word FROM cards WHERE word LIKE '%bedrift%'").fetchall()
print("raw DB words like bedrift:", [r['word'] for r in rows])
