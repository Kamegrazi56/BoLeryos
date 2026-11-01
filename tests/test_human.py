import asyncio
import types
from dataclasses import replace

import pytest

from app import human
from app.config import Settings


class FakeKeyboard:
    def __init__(self):
        self.typed = []

    async def type(self, char):
        self.typed.append(char)


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()


@pytest.fixture
def sleeps(monkeypatch):
    """Enregistre les pauses demandées par app.human, sans toucher à asyncio.sleep des autres threads
    (le serveur du faux site, encore actif en arrière-plan, l'utilise aussi)."""
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(human, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
    return recorded


def test_think_pause_is_short_and_random(sleeps):
    s = Settings(human_delay_min=0.4, human_delay_max=1.4)
    for _ in range(50):
        asyncio.run(human.think(s))
    assert len(sleeps) == 50
    assert all(0.4 <= d <= 1.4 for d in sleeps)
    assert len(set(sleeps)) > 10  # pas de durée fixe


def test_typing_is_character_by_character_with_irregular_rhythm(sleeps):
    s = Settings(key_delay_min=0.05, key_delay_max=0.18)
    page = FakePage()
    text = "Mot-de-passe-test!" * 5
    asyncio.run(human.type_text(page, text, s))
    assert "".join(page.keyboard.typed) == text
    assert len(sleeps) == len(text)  # un délai après chaque touche
    assert all(0.05 <= d <= 0.18 + 0.5 for d in sleeps)  # hésitation occasionnelle : +0,2 à +0,5 s
    assert len(set(sleeps)) > 10


def test_human_mode_off_has_no_delay(sleeps):
    s = replace(Settings(), human_mode=False)
    page = FakePage()
    asyncio.run(human.think(s))
    asyncio.run(human.micro(s))
    asyncio.run(human.type_text(page, "abc", s))
    assert sleeps == []
    assert page.keyboard.typed == ["a", "b", "c"]
