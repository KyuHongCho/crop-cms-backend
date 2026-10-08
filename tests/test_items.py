"""HTTP-level smoke test for the item endpoints.

There is deliberately no POST /crops (crops are seeded from the advisor's data), so the fixture
crop is inserted directly, not over HTTP.
"""
import pytest
from sqlalchemy import text

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


def test_create_item_then_it_appears_in_the_list(editor_client):
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

    created = editor_client.post("/items", json=body)
    assert created.status_code == 201

    listed = editor_client.get("/items")
    assert listed.status_code == 200
    titles = [item["title"] for item in listed.json()]
    assert "a document" in titles


# --- DELETE /items/{item_id} --------------------------------------------------

ITEM_COLUMNS = (
    "id, sub_category_id, crop_id, topic, title, body, published, source, reference, url, "
    "read_directly, via, condition, licence_note"
)


def _sql(statement, **params):
    with sync_engine.begin() as connection:
        return connection.execute(text(statement), params)


def _seed_item(crop_id: int, published: bool = True) -> int:
    return _sql(
        "INSERT INTO items (sub_category_id, crop_id, topic, title, body, published, source, "
        "reference, url, read_directly, via, condition, licence_note) "
        "VALUES (:sub, :crop, 'optimal-temperature', 'doc', 'body text', :published, 's', 'r', "
        "'u', false, 'a via', 'a condition', 'a licence') RETURNING id",
        sub=UNCATEGORISED_SUB_CATEGORY_ID, crop=crop_id, published=published,
    ).scalar_one()


def _seed_chunk(item_id: int) -> None:
    zeros = "[" + ",".join(["0"] * 1536) + "]"
    _sql(
        "INSERT INTO item_chunks (item_id, chunk_index, content, content_hash, embedding, model) "
        "VALUES (:item, 0, 'c', 'h', CAST(:embedding AS vector), 'fake')",
        item=item_id, embedding=zeros,
    )


def _row(item_id: int):
    return _sql(f"SELECT {ITEM_COLUMNS} FROM items WHERE id = :id", id=item_id).mappings().first()


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_delete_returns_the_stored_row_and_removes_it(client_with_role, role):
    item_id = _seed_item(_seed_crop())
    stored = dict(_row(item_id))

    response = client_with_role(role).delete(f"/items/{item_id}")

    assert response.status_code == 200
    assert response.json() == stored
    assert _row(item_id) is None
    assert item_id not in [item["id"] for item in client_with_role(role).get("/items").json()]


def test_a_second_delete_and_an_unknown_id_are_404(editor_client):
    item_id = _seed_item(_seed_crop())
    assert editor_client.delete(f"/items/{item_id}").status_code == 200

    assert editor_client.delete(f"/items/{item_id}").status_code == 404
    assert editor_client.delete(f"/items/{item_id}").json() == {"detail": "Item not found"}
    assert editor_client.delete("/items/0").status_code == 404
    assert editor_client.delete("/items/-1").status_code == 404


def test_delete_removes_the_chunks_and_the_published_view_rows(editor_client):
    item_id = _seed_item(_seed_crop(), published=True)
    _seed_chunk(item_id)
    chunks = "SELECT count(*) FROM item_chunks WHERE item_id = :id"
    visible = "SELECT count(*) FROM published_item_chunks WHERE source_id = :id"
    assert _sql(chunks, id=item_id).scalar_one() == 1
    assert _sql(visible, id=item_id).scalar_one() == 1

    assert editor_client.delete(f"/items/{item_id}").status_code == 200

    assert _sql(chunks, id=item_id).scalar_one() == 0
    assert _sql(visible, id=item_id).scalar_one() == 0


def test_delete_needs_a_token_and_the_editor_role(client, client_with_role):
    item_id = _seed_item(_seed_crop())
    before = dict(_row(item_id))

    assert client.delete(f"/items/{item_id}").status_code == 401
    assert client_with_role("member").delete(f"/items/{item_id}").status_code == 403

    assert dict(_row(item_id)) == before


@pytest.mark.parametrize("item_id", [2**31, -(2**31) - 1])
def test_delete_with_an_id_outside_int4_is_422_not_500(editor_client, item_id):
    assert editor_client.delete(f"/items/{item_id}").status_code == 422


@pytest.mark.parametrize("item_id", [2**31 - 1, -(2**31), 0, -1])
def test_delete_with_an_id_at_the_int4_edge_is_404(editor_client, item_id):
    response = editor_client.delete(f"/items/{item_id}")
    assert (response.status_code, response.json()) == (404, {"detail": "Item not found"})


@pytest.mark.parametrize("item_id", [99999999999, 0, -5])
def test_a_refused_caller_with_an_out_of_range_or_unknown_id_is_401_or_403_not_422_or_404(
    client, client_with_role, item_id
):
    assert client.delete(f"/items/{item_id}").status_code == 401
    assert client_with_role("member").delete(f"/items/{item_id}").status_code == 403
