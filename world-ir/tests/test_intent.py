"""Intent: deliberate breaks from the default checks must quote words the user really gave."""

import copy
import json
import pathlib
import sys

import pytest
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World, intent_changes  # noqa: E402
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


# Builder intents ---------------------------------------------------------------------


def box(data):
    return next(n for n in data["nodes"][0]["children"] if n["id"] == "fallen_box")


def test_builder_may_decide_oddness_the_brief_licenses():
    world = World.model_validate(DATA)
    intent = world.waiver("fallen_box", "upright")
    assert intent.source == "builder" and intent.licence == "horror" and intent.quote is None


def test_builder_licence_must_be_in_the_brief(data):
    data["brief"]["mood"] = ["uneasy"]
    with pytest.raises(ValidationError, match="not in the brief"):
        World.model_validate(data)


def test_builder_licence_must_allow_oddness(data):
    data["brief"]["mood"].append("cosy")
    box(data)["intent"][0]["licence"] = "cosy"
    with pytest.raises(ValidationError, match="does not allow oddness"):
        World.model_validate(data)


def test_builder_must_say_why(data):
    del box(data)["intent"][0]["note"]
    with pytest.raises(ValidationError, match="licence and a note"):
        World.model_validate(data)


def test_builder_cannot_waive_hard_checks(data):
    box(data)["intent"][0]["allows"] = ["door_clearance"]
    with pytest.raises(ValidationError, match="only the user can waive door_clearance"):
        World.model_validate(data)


def test_builder_intents_are_capped(data):
    data.setdefault("rules", {})["max_builder_intents"] = 0
    with pytest.raises(ValidationError, match="max_builder_intents"):
        World.model_validate(data)


def test_intent_is_frozen_once_checking_starts(data):
    built = World.model_validate(data)
    # A repair step that moves things is fine.
    moved = copy.deepcopy(data)
    box(moved)["xform"]["pos"] = [1.1, 0.17, 2.4]
    assert intent_changes(built, World.model_validate(moved)) == []
    # Excusing the bed after the validators saw it is not.
    excused = copy.deepcopy(data)
    bed = next(n for n in excused["nodes"][0]["children"] if n["id"] == "bed")
    bed["intent"].append(
        {"allows": ["upright"], "source": "builder", "licence": "horror", "note": "crooked on purpose"}
    )
    assert intent_changes(built, World.model_validate(excused)) == ["'bed': intent changed"]
    # The user can still add intent at any time.
    asked = copy.deepcopy(data)
    asked["brief"]["messages"].append("leave the desk chair where it is")
    chair(asked)["intent"].append({"allows": ["facing"], "source": "user", "quote": "leave the desk chair where it is"})
    assert intent_changes(built, World.model_validate(asked)) == []
