"""Intent: deliberate breaks from the default checks must quote words the user really gave."""

import copy
import json
import pathlib
import sys

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.common import PROMPT_WAIVABLE  # noqa: E402

DATA = json.loads((ROOT / "examples" / "horror_room.json").read_text())


@pytest.fixture
def data():
    return copy.deepcopy(DATA)


def chair(data):
    return next(n for n in data["nodes"][0]["children"] if n["id"] == "ceiling_chair")


def test_horror_room_waives_only_what_was_asked():
    world = World.model_validate(DATA)
    assert world.waiver("ceiling_chair", "upright").source == "prompt"
    assert world.waiver("ceiling_chair", "floating") is None  # still checked against the ceiling
    assert world.waiver("bed", "upright") is None
    assert world.waiver("nightstand", "upright").source == "user"


def test_waivers_cover_children():
    world = World.model_validate(DATA)
    assert world.waiver("lamp_glow", "floating").quote == "a lamp floating in mid-air"


def test_quote_must_appear_in_the_prompt(data):
    chair(data)["intent"][0]["quote"] = "a chair glued to the ceiling"
    with pytest.raises(ValidationError, match="not in the prompt"):
        World.model_validate(data)


def test_quotes_match_whole_words_ignoring_case_and_punctuation(data):
    chair(data)["intent"][0]["quote"] = "A CHAIR stuck upside-down, on the ceiling"
    World.model_validate(data)
    chair(data)["intent"][0]["quote"] = "mid air"  # the prompt says "mid-air"
    World.model_validate(data)
    chair(data)["intent"][0]["quote"] = "hair"  # "c-hair" is not a word in the prompt
    with pytest.raises(ValidationError):
        World.model_validate(data)


def test_prompt_cannot_waive_hard_checks_but_the_user_can(data):
    chair(data)["intent"][0]["allows"] = ["inside_wall"]
    with pytest.raises(ValidationError, match="only the user can waive inside_wall"):
        World.model_validate(data)
    chair(data)["intent"][0]["source"] = "user"
    World.model_validate(data)  # user messages include the prompt
    assert "inside_wall" not in PROMPT_WAIVABLE


def test_user_quotes_come_from_user_messages(data):
    chair(data)["intent"][0].update(source="user", quote="keep the nightstand tipped over")
    World.model_validate(data)
    data["brief"]["messages"] = []
    with pytest.raises(ValidationError, match="user's messages"):
        World.model_validate(data)


def test_image_quotes_need_an_approved_reference(data):
    chair(data)["intent"][0].update(source="image_brief", quote="chair hanging from the ceiling")
    data["brief"]["references"] = [{"url": "https://example.com/a.jpg", "caption": "A chair hanging from the ceiling."}]
    with pytest.raises(ValidationError, match="approved reference"):
        World.model_validate(data)
    data["brief"]["references"][0]["approved"] = True
    World.model_validate(data)


def test_intent_needs_a_brief(data):
    del data["brief"]
    with pytest.raises(ValidationError, match="needs a brief"):
        World.model_validate(data)


def test_near_misses_are_still_mistakes():
    rules = World.model_validate(DATA).rules
    assert rules.deliberate_min_offset > rules.float_tolerance
    assert rules.deliberate_min_tilt_deg > 0
