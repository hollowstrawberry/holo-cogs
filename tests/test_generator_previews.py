import asyncio
import base64
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from aiohttp import ClientSession, web

COG_DIR = Path(__file__).resolve().parents[1] / "arcenciel"


def load_module(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def preview_modules(monkeypatch):
    package = types.ModuleType("arcenciel")
    package.__path__ = [str(COG_DIR)]
    monkeypatch.setitem(sys.modules, "arcenciel", package)

    def stub(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    class Context:
        pass

    core = stub("redbot.core", commands=stub("redbot.core.commands", Context=Context))
    stub("redbot", core=core)
    stub("humanize")
    stub("arcenciel.base", ArcencielBase=object)
    stub(
        "arcenciel.utils",
        ImageGenError=RuntimeError,
        clean_model=lambda value: value,
        parse_prompts=lambda payload: None,
        is_nsfw=lambda channel: channel.nsfw,
        build_split_masks=lambda *args: [],
        send_response=lambda *args, **kwargs: None,
        gather_raise_all=lambda *args: None,
    )
    stub("arcenciel.comfy", ComfyMetadata=object, ComfyMetadataReader=object)
    stub("arcenciel.schema", ImageGenParams=object, QueuedImageGen=object)
    stub("arcenciel.commands", ArcencielCommands=object)
    stub("arcenciel.views")
    stub("arcenciel.views.generating", GeneratingView=object)
    stub("arcenciel.views.image_actions", ImageActions=object)
    stub("arcenciel.constants", JOB_TIMEOUT=600, PROGRESS_UPDATE_INTERVAL=5, ENDPOINT="https://example.test/api")

    api = load_module("arcenciel.arcenciel_api", COG_DIR / "arcenciel_api.py", monkeypatch)
    cog = load_module("arcenciel.arcenciel", COG_DIR / "arcenciel.py", monkeypatch)

    class Member:
        display_name = "Artist"
        display_avatar = SimpleNamespace(url="https://example.test/avatar.png")

    class Channel:
        def __init__(self, nsfw):
            self.nsfw = nsfw

    monkeypatch.setattr(cog.discord, "Member", Member)
    monkeypatch.setattr(cog.discord.abc, "Messageable", Channel)
    return api, cog, Context, Member, Channel


class ProgressMessage:
    def __init__(self, upload_error=None):
        self.edits = []
        self.upload_error = upload_error
        self.on_upload = None

    async def edit(self, **kwargs):
        if kwargs.get("attachments") and self.upload_error:
            raise self.upload_error
        if kwargs.get("attachments") and self.on_upload:
            self.on_upload()
        self.edits.append(kwargs)


def make_job(cog_module, context_type, member_type, channel_type, nsfw):
    channel = channel_type(nsfw)
    context = context_type()
    context.channel = channel
    context.author = member_type()
    message = ProgressMessage()
    gen = SimpleNamespace(
        id="job-123",
        channel=channel,
        context=context,
        progress_message=message,
        last_updated=datetime.now(timezone.utc) - timedelta(seconds=10),
        last_eta=1_000_000,
        last_percent=0,
        last_position=-1,
        pending_preview=None,
        preview_version=0,
        displayed_preview_version=0,
        preview_filename=None,
        preview_step=0,
        preview_total_steps=0,
        preview_disabled=False,
        cancelled=False,
    )
    instance = object.__new__(cog_module.Arcenciel)

    async def color(_channel):
        return discord.Color.blue()

    async def loading_emoji():
        return "⏳"

    instance.bot = SimpleNamespace(get_embed_color=color)
    instance.config = SimpleNamespace(loading_emoji=loading_emoji)
    instance.queued_images = {gen.id: gen}
    job = {
        "id": gen.id,
        "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "status": "running",
        "progress": {"phase": "sampling", "percent": 23, "etaMs": 22000},
        "position": 0,
        "queueEtaMs": 0,
    }
    return instance, gen, job, message


@pytest.mark.asyncio
async def test_stream_reads_only_preview_events(preview_modules):
    api_module = preview_modules[0]
    expected = {"jobId": "job-123", "step": 4, "totalSteps": 24}
    lines = [
        b"event: connected\n",
        b'data: {"ok":true}\n',
        b"\n",
        b"event: preview\n",
        f"data: {json.dumps(expected)}\n".encode(),
        b"\n",
        b"event: heartbeat\n",
        b'data: {"ts":1}\n',
        b"\n",
    ]

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        @property
        def content(self):
            async def iterate():
                for line in lines:
                    yield line

            return SimpleNamespace(iter_chunked=lambda size: iterate())

    class Session:
        def get(self, url, **kwargs):
            assert url == "https://example.test/api/generator/events"
            assert kwargs["headers"]["Accept"] == "text/event-stream"
            assert kwargs["timeout"].total is None
            return Response()

    client = object.__new__(api_module.ArcEnCielAPI)
    client.endpoint = "https://example.test/api"
    client.session = Session()
    assert [event async for event in client.stream_previews()] == [expected]


@pytest.mark.asyncio
async def test_stream_accepts_full_size_preview_line(preview_modules):
    api_module = preview_modules[0]
    payload = {"jobId": "job-123", "imageBase64": base64.b64encode(b"x" * 524000).decode()}

    async def handler(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(f"event: preview\ndata: {json.dumps(payload)}\n\n".encode())
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/generator/events", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            client = object.__new__(api_module.ArcEnCielAPI)
            client.endpoint = f"http://127.0.0.1:{port}"
            client.session = session
            assert [event async for event in client.stream_previews()] == [payload]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_sfw_keeps_progress_without_preview(preview_modules):
    _, cog_module, Context, Member, Channel = preview_modules
    cog, gen, job, message = make_job(cog_module, Context, Member, Channel, nsfw=False)
    gen.pending_preview = (b"preview", "image/jpeg", 4, 24)
    gen.preview_version = 1

    await cog.update_job(job, gen)

    edit = message.edits[-1]
    fields = {field.name: field.value for field in edit["embed"].fields}
    assert fields["Progress"] == "`23%`"
    assert fields["ETA"].startswith("<t:")
    assert "Preview step" not in fields
    assert "attachments" not in edit


@pytest.mark.asyncio
async def test_nsfw_preview_survives_later_progress_edit(preview_modules):
    _, cog_module, Context, Member, Channel = preview_modules
    cog, gen, job, message = make_job(cog_module, Context, Member, Channel, nsfw=True)
    gen.pending_preview = (b"preview", "image/jpeg", 4, 24)
    gen.preview_version = 1

    await cog.update_job(job, gen)

    first = message.edits[-1]
    assert len(first["attachments"]) == 1
    assert first["embed"].image.url == f"attachment://{gen.preview_filename}"
    assert {field.name: field.value for field in first["embed"].fields}["Preview step"] == "`4/24`"

    gen.last_updated -= timedelta(seconds=10)
    job["progress"]["percent"] = 27
    await cog.update_job(job, gen)

    second = message.edits[-1]
    assert "attachments" not in second
    assert second["embed"].image.url == first["embed"].image.url
    assert {field.name: field.value for field in second["embed"].fields}["Progress"] == "`27%`"

    gen.channel.nsfw = False
    gen.last_updated -= timedelta(seconds=10)
    await cog.update_job(job, gen)
    third = message.edits[-1]
    assert third["attachments"] == []
    assert not third["embed"].image.url
    assert {field.name: field.value for field in third["embed"].fields}["Progress"] == "`27%`"


@pytest.mark.asyncio
async def test_preview_upload_error_keeps_progress(preview_modules, monkeypatch):
    _, cog_module, Context, Member, Channel = preview_modules

    class UploadError(Exception):
        pass

    monkeypatch.setattr(cog_module.discord, "HTTPException", UploadError)
    cog, gen, job, message = make_job(cog_module, Context, Member, Channel, nsfw=True)
    message.upload_error = UploadError("upload failed")
    gen.pending_preview = (b"preview", "image/jpeg", 4, 24)
    gen.preview_version = 1

    await cog.update_job(job, gen)

    assert gen.preview_disabled is True
    assert gen.pending_preview is None
    assert {field.name: field.value for field in message.edits[-1]["embed"].fields}["Progress"] == "`23%`"


@pytest.mark.asyncio
async def test_new_frame_during_upload_remains_pending(preview_modules):
    _, cog_module, Context, Member, Channel = preview_modules
    cog, gen, job, message = make_job(cog_module, Context, Member, Channel, nsfw=True)
    gen.pending_preview = (b"first", "image/jpeg", 4, 24)
    gen.preview_version = 1

    def receive_next_frame():
        gen.pending_preview = (b"second", "image/jpeg", 8, 24)
        gen.preview_version = 2

    message.on_upload = receive_next_frame
    await cog.update_job(job, gen)

    assert gen.displayed_preview_version == 1
    assert gen.pending_preview == (b"second", "image/jpeg", 8, 24)


@pytest.mark.asyncio
async def test_stream_dispatches_only_matching_nsfw_jobs(preview_modules):
    _, cog_module, Context, Member, Channel = preview_modules
    cog, gen, _, _ = make_job(cog_module, Context, Member, Channel, nsfw=True)
    other = make_job(cog_module, Context, Member, Channel, nsfw=False)[1]
    other.id = "sfw-job"
    cog.queued_images[other.id] = other
    session = SimpleNamespace(closed=False)

    async def stream_previews():
        for job_id, step in (("unknown", 4), (other.id, 4), (gen.id, 4), (gen.id, 8)):
            yield {
                "jobId": job_id,
                "mimeType": "image/jpeg",
                "step": step,
                "totalSteps": 24,
                "imageBase64": base64.b64encode(b"preview").decode(),
            }
        session.closed = True

    cog.api = SimpleNamespace(session=session, stream_previews=stream_previews)
    await asyncio.wait_for(cog.consume_previews(), timeout=1)

    assert gen.pending_preview == (b"preview", "image/jpeg", 8, 24)
    assert gen.preview_version == 2
    assert other.pending_preview is None
