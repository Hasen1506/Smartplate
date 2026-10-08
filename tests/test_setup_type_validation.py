"""A list where a single choice belongs is a 400, not a 500 (found by the property fuzzer in
tests/test_gt_properties.py once a new route shifted its examples: {"goal": []} raised
TypeError because GOALS is a dict and a list can't be looked up in it)."""
import pytest

from smartplate.app import create_app
from smartplate.domain import profile


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("field, value", [("goal", []), ("variety", ["mixed"]), ("diet", ["veg"]),
                                          ("cuisine_tilt", {"cuisine": ["south_indian"]}),
                                          ("cuisine_tilt", {"cuisine": "south_indian", "strength": {}})])
def test_unhashable_choice_is_a_validation_error(field, value):
    with pytest.raises(ValueError):
        profile.validate_setup({field: value})


def test_budget_suggestion_with_a_list_goal_is_a_400(client):
    r = client.post("/api/suggest-budget", json={"goal": [], "meals": ["lunch"]})
    assert r.status_code == 400, r.get_json()
