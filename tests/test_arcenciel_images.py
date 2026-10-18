import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest


@pytest.fixture
def image_tool(monkeypatch):
    def package(name):
        module = types.ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)

    package("agent")
    package("agent.tools")

    class Schema:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    def stub(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    stub("agent.schema", ToolCall=Schema, Function=Schema, Parameters=Schema)
    stub("agent.tools.base", ToolBase=object)
    path = (
        Path(__file__).resolve().parents[1] / "agent" / "tools" / "arcenciel_images.py"
    )
    spec = importlib.util.spec_from_file_location("agent.tools.arcenciel_images", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, data, error=None):
        self.data = data
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def json(self):
        return self.data


class FakeSession:
    def __init__(self, responses, key="existing-bot-key"):
        self.headers = {"x-api-key": key}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def make_tool(tool_type, session, nsfw=False, parent_nsfw=False):
    tool = object.__new__(tool_type)
    api = SimpleNamespace(endpoint="https://example.test/api", session=session)
    bot = SimpleNamespace(
        get_cog=lambda name: SimpleNamespace(api=api) if name == "Arcenciel" else None
    )
    tool.ctx = SimpleNamespace(
        bot=bot,
        channel=SimpleNamespace(nsfw=nsfw, parent=SimpleNamespace(nsfw=parent_nsfw)),
    )
    return tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nsfw", "expected_ratings", "expected_ids"),
    [
        (False, "questionable,safe", [1, 2]),
        (True, "explicit,questionable,safe,sensitive", [1, 2, 3, 4]),
    ],
)
async def test_channel_ratings_and_published_results(
    image_tool, nsfw, expected_ratings, expected_ids
):
    images = [
        {
            "id": 1,
            "rating": "safe",
            "status": "PUBLISHED",
            "title": None,
            "uploader": {"username": "Artist"},
        },
        {
            "id": 2,
            "rating": "questionable",
            "status": "PUBLISHED",
            "title": "Portrait",
            "uploader": {"username": "Artist"},
        },
        {
            "id": 3,
            "rating": "sensitive",
            "status": "PUBLISHED",
            "title": None,
            "uploader": {"username": "Artist"},
        },
        {
            "id": 4,
            "rating": "explicit",
            "status": "PUBLISHED",
            "title": None,
            "uploader": {"username": "Artist"},
        },
        {
            "id": 5,
            "rating": "safe",
            "status": "DRAFT",
            "title": "Private",
            "uploader": {"username": "Artist"},
        },
    ]
    session = FakeSession([FakeResponse({"data": images})])
    result = await make_tool(image_tool.ArcencielImagesTool, session, nsfw).run(
        {"query": "fubuki", "sort": "newest"}
    )

    url, kwargs = session.calls[0]
    assert url == "https://example.test/api/images/search"
    assert kwargs["params"] == {
        "search": "fubuki",
        "sort": "newest",
        "page": 1,
        "limit": 5,
        "ratings": expected_ratings,
    }
    assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
    matches = result["results"]["image"]
    assert [item["@id"] for item in matches] == expected_ids
    assert matches[0] == {
        "@id": 1,
        "@title": "Image #1",
        "url": "https://arcenciel.io/images/1",
        "uploader": "Artist",
        "rating": "safe",
    }
    for item in matches:
        if item["rating"] in ("sensitive", "explicit"):
            assert item["url"] == f"||https://arcenciel.io/images/{item['@id']}||"
        else:
            assert item["url"] == f"https://arcenciel.io/images/{item['@id']}"


@pytest.mark.asyncio
async def test_exact_uploader_lookup_and_pagination(image_tool):
    session = FakeSession(
        [
            FakeResponse(
                [{"id": 7, "username": "Other"}, {"id": 5, "username": "Fubuker"}]
            ),
            FakeResponse(
                {
                    "data": [
                        {
                            "id": 42,
                            "rating": "safe",
                            "status": "PUBLISHED",
                            "title": "Found",
                        }
                    ]
                }
            ),
        ]
    )
    result = await make_tool(image_tool.ArcencielImagesTool, session).run(
        {"query": "fox", "user": "fubuker", "page": 2, "sort": "reactions"}
    )

    assert session.calls[0][0] == "https://example.test/api/users/search"
    assert session.calls[1][1]["params"] == {
        "search": "fox",
        "sort": "reactions",
        "page": 2,
        "limit": 5,
        "ratings": "questionable,safe",
        "userId": 5,
    }
    assert result["results"]["image"][0]["url"] == "https://arcenciel.io/images/42"


def test_nsfw_thread_inherits_parent_rating(image_tool):
    assert image_tool.channel_is_nsfw(
        SimpleNamespace(parent=SimpleNamespace(nsfw=True))
    )


@pytest.mark.asyncio
async def test_missing_key_and_search_error(image_tool):
    missing_key = FakeSession([], key="")
    assert "not configured" in await make_tool(
        image_tool.ArcencielImagesTool, missing_key
    ).run({})
    assert not missing_key.calls

    failure = FakeSession(
        [FakeResponse(None, aiohttp.ClientError("Service unavailable"))]
    )
    assert "Failed to search" in await make_tool(
        image_tool.ArcencielImagesTool, failure
    ).run({"query": "fox"})


@pytest.mark.parametrize(
    ("input_text", "expected"),
    [
        (
            "[Adult](https://arcenciel.io/images/42) and https://arcenciel.io/images/42",
            "Adult: ||https://arcenciel.io/images/42|| and ||https://arcenciel.io/images/42||",
        ),
        (
            "[Adult](||https://arcenciel.io/images/42||) or ||[Adult](https://arcenciel.io/images/42)||",
            "Adult: ||https://arcenciel.io/images/42|| or Adult: ||https://arcenciel.io/images/42||",
        ),
        (
            "||https://arcenciel.io/images/42|| and [Safe](https://arcenciel.io/images/7)",
            "||https://arcenciel.io/images/42|| and [Safe](https://arcenciel.io/images/7)",
        ),
        (
            "<https://arcenciel.io/images/42> and <https://arcenciel.io/images/7>",
            "||https://arcenciel.io/images/42|| and <https://arcenciel.io/images/7>",
        ),
    ],
)
def test_final_reply_spoilers_only_adult_links(image_tool, input_text, expected):
    assert image_tool.spoiler_adult_image_links(input_text, {42}) == expected
