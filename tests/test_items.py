"""HTTP-level smoke test for the item endpoints.

There is deliberately no POST /crops (see app/schema/crop.py) -- crops are
seeded from the advisor's ECOCROP data, not authored via the API. So the
fixture crop here is inserted directly, not over HTTP.
"""
from app.db.migrate_db import engine as sync_engine
from app.model.model import UNCATEGORISED_SUB_CATEGORY_ID, Crop


def _seed_crop(slug: str = "basil") -> int:
    with sync_engine.begin() as connection:
        result = connection.execute(
            Crop.__table__.insert().values(
                slug=slug, common_name=slug, scientific_name="Ocimum basilicum"
            )
        )
        return result.inserted_primary_key[0]


def test_create_item_then_it_appears_in_the_list(client):
    crop_id = _seed_crop()
    body = {
        "sub_category_id": UNCATEGORISED_SUB_CATEGORY_ID,
        "crop_id": crop_id,
        "title": "a document",
        "body": "some body text",
        "source": "s",
        "reference": "r",
        "url": "u",
        "read_directly": True,
    }

    created = client.post("/items", json=body)
    assert created.status_code == 201

    listed = client.get("/items")
    assert listed.status_code == 200
    titles = [item["title"] for item in listed.json()]
    assert "a document" in titles
