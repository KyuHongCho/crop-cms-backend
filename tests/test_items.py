"""HTTP-level smoke test for the item endpoints.

There is deliberately no POST /crops (crops are seeded from the advisor's data), so the fixture
crop is inserted directly, not over HTTP.
"""
import pytest
from sqlalchemy import text

from app.db.migrate_db import engine as sync_engine
from app.model.model import UNCATEGORISED_SUB_CATEGORY_ID, Crop
from app.schema.item import MAX_OFFSET


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

    listed = editor_client.get("/items?status=all")
    assert listed.status_code == 200
    titles = [item["title"] for item in listed.json()]
    assert "a document" in titles


# --- GET /items ---------------------------------------------------------------

def _ids(response):
    return [item["id"] for item in response.json()]


def _seed_published_and_draft():
    crop_id = _seed_crop()
    return _seed_item(crop_id, published=True), _seed_item(crop_id, published=False)


def test_anonymous_list_is_published_only(client):
    published, draft = _seed_published_and_draft()

    response = client.get("/items")

    assert response.status_code == 200
    assert _ids(response) == [published]
    assert _ids(client.get("/items?status=published")) == [published]


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_status_all_shows_drafts_to_editors_and_admins(client_with_role, role):
    published, draft = _seed_published_and_draft()
    staff = client_with_role(role)

    assert _ids(staff.get("/items?status=all")) == [published, draft]
    assert _ids(staff.get("/items")) == [published]


def test_status_all_without_a_token_is_403_not_a_list(client):
    _seed_published_and_draft()

    response = client.get("/items?status=all")

    assert response.status_code == 403
    assert response.json() == {"detail": "Not enough permissions"}


def test_status_all_for_a_plain_member_is_403(client_with_role):
    _seed_published_and_draft()

    response = client_with_role("member").get("/items?status=all")

    assert response.status_code == 403
    assert response.json() == {"detail": "Not enough permissions"}


def test_a_garbage_token_is_401_on_status_all_and_ignored_on_the_default(client):
    published, _ = _seed_published_and_draft()
    headers = {"Authorization": "Bearer not-a-token"}

    assert client.get("/items?status=all", headers=headers).status_code == 401
    default = client.get("/items", headers=headers)
    assert default.status_code == 200
    assert _ids(default) == [published]


def test_an_unknown_status_is_422(client):
    assert client.get("/items?status=bogus").status_code == 422


def test_a_deactivated_editor_is_401_on_status_all(editor_client):
    _sql("UPDATE members SET is_active = false WHERE id = :id", id=editor_client.member_id)

    assert editor_client.get("/items?status=all").status_code == 401


def _seed_published(count: int) -> list[int]:
    crop_id = _seed_crop()
    return [_seed_item(crop_id, published=True) for _ in range(count)]


def test_limit_and_offset_window_the_id_ordered_list(client):
    first, second, third = _seed_published(3)

    assert _ids(client.get("/items?limit=2")) == [first, second]
    assert _ids(client.get("/items?limit=2&offset=2")) == [third]
    assert client.get("/items?offset=3").json() == []


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=501", "limit=-1", "offset=-1", "limit=abc"]
)
def test_bad_paging_parameters_are_422(client, query):
    assert client.get(f"/items?{query}").status_code == 422


def test_offset_is_bounded_so_it_cannot_overflow_a_bigint(client):
    _seed_published(1)

    at_max = client.get(f"/items?offset={MAX_OFFSET}")
    assert at_max.status_code == 200
    assert at_max.json() == []
    assert client.get(f"/items?offset={MAX_OFFSET + 1}").status_code == 422


def test_paging_composes_with_status(client, editor_client):
    crop_id = _seed_crop()
    p1 = _seed_item(crop_id, published=True)
    draft = _seed_item(crop_id, published=False)
    p2 = _seed_item(crop_id, published=True)

    assert _ids(client.get("/items?limit=1")) == [p1]
    assert _ids(client.get("/items?limit=1&offset=1")) == [p2]
    assert _ids(editor_client.get("/items?status=all&limit=2&offset=1")) == [draft, p2]


def test_the_default_returns_every_published_item(client):
    ids = _seed_published(5)

    assert _ids(client.get("/items")) == ids


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
    assert item_id not in [item["id"] for item in client_with_role(role).get("/items?status=all").json()]


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
