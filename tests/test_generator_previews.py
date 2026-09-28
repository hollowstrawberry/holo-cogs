import asyncio
import base64
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from aiohttp import ClientSession, web
from discord.http import handle_message_parameters

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
    load_module("arcenciel.schema", COG_DIR / "schema.py", monkeypatch)
    stub("arcenciel.commands", ArcencielCommands=object)
    stub("arcenciel.views")
    stub("arcenciel.views.image_actions", ImageActions=object)
    stub("arcenciel.constants", JOB_TIMEOUT=600, PROGRESS_UPDATE_INTERVAL=5, VIEW_TIMEOUT=900,
         ENDPOINT="https://example.test/api")

    load_module("arcenciel.views.generating", COG_DIR / "views" / "generating.py", monkeypatch)
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

    def delete(self):
        return None


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
        callback=None,
        user=member_type(),
        progress_v2_view=None,
    )
    instance = object.__new__(cog_module.Arcenciel)

    async def color(_channel):
        return discord.Color.blue()

    async def loading_emoji():
        return "⏳"

    instance.bot = SimpleNamespace(get_embed_color=color)
    instance.config = SimpleNamespace(loading_emoji=loading_emoji)
    instance.queued_images = {gen.id: gen}
    if nsfw:
        gen.progress_v2_view = cog_module.GeneratingV2View(instance, gen, discord.Color.blue())
    job = {
        "id": gen.id,
        "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "status": "running",
        "progress": {"phase": "sampling", "percent": 23, "etaMs": 22000},
        "position": 0,
        "queueEtaMs": 0,
    }
    return instance, gen, job, message


def v2_children(edit):
    assert edit.get("embed") is None
    container, = edit["view"].to_components()
    assert container["type"] == 17
    assert container["spoiler"] is False
    return container["components"]


def v2_text(children):
    return " ".join(item["content"] for item in children if item["type"] == 10)


def v2_gallery(children):
    return [item for item in children if item["type"] == 12]


@pytest.mark.asyncio
@pytest.mark.parametrize("nsfw", [False, True])
@pytest.mark.parametrize("interaction", [False, True])
async def test_progress_message_starts_in_the_correct_layout(preview_modules, monkeypatch, nsfw, interaction):
    _, cog_module, Context, Member, Channel = preview_modules
    monkeypatch.setattr(cog_module.discord, "TextChannel", Channel)
    monkeypatch.setattr(cog_module.discord, "Thread", Channel)
    if interaction:
        class Interaction:
            pass

        monkeypatch.setattr(cog_module.discord, "Interaction", Interaction)
        context = Interaction()
        context.user = Member()
    else:
        context = Context()
        context.author = Member()
    context.channel = Channel(nsfw)
    context.guild = object()
    sent = {}

    async def reply(**kwargs):
        sent.update(kwargs)
        return ProgressMessage()

    if interaction:
        async def edit_original_response(**kwargs):
            sent.update(kwargs)

        context.edit_original_response = edit_original_response
    else:
        context.message = SimpleNamespace(id=123)
        context.reply = reply
    cog = object.__new__(cog_module.Arcenciel)

    async def color(_channel):
        return discord.Color.blue()

    async def yes(*args):
        return True

    async def no(*args):
        return False

    async def request_image(_payload):
        return {"id": "job-123"}

    class GuildConfig:
        enabled = yes

    cog.bot = SimpleNamespace(get_embed_color=color)
    cog.config = SimpleNamespace(guild=lambda _guild: GuildConfig(), loading_emoji=lambda: loading_emoji())
    cog.api = SimpleNamespace(request_image=request_image)
    cog.queued_images = {}
    cog.check_quota = yes
    cog.contains_blacklisted_word = no

    async def loading_emoji():
        return "⏳"

    await cog.generate_image(context, payload={"prompt": "safe test"})

    gen = cog.queued_images["job-123"]
    assert (gen.progress_v2_view is not None) is nsfw
    if nsfw:
        assert sent.get("embed") is None
        assert sent["view"].to_components()[0]["type"] == 17
        assert not v2_gallery(v2_children(sent))
        if interaction:
            assert sent["content"] is None
            assert sent["attachments"] == []
    else:
        assert sent["embed"].description == "⏳ Image request sent..."


@pytest.mark.asyncio
async def test_nsfw_interaction_final_uses_followup_and_removes_v2_progress(preview_modules, monkeypatch):
    _, cog_module, Context, Member, Channel = preview_modules
    cog, gen, _, _ = make_job(cog_module, Context, Member, Channel, nsfw=True)
    final_message = SimpleNamespace(id=456)
    fetched_message = SimpleNamespace(id=456)
    calls = []

    class Interaction:
        followup = SimpleNamespace(send=None)

        async def delete_original_response(self):
            calls.append("delete-progress")

    async def send(**kwargs):
        calls.append(("send-final", kwargs))
        return final_message

    async def fetch_message(message_id):
        calls.append(("fetch-final", message_id))
        return fetched_message

    Interaction.followup.send = send
    monkeypatch.setattr(cog_module.discord, "Interaction", Interaction)
    gen.context = Interaction()
    gen.channel.fetch_message = fetch_message

    result = await cog.send_generation_response(gen, content="done")

    assert result is fetched_message
    assert calls == [("send-final", {"content": "done"}), "delete-progress", ("fetch-final", 456)]


def test_v2_preview_wire_payload_contains_one_spoiler_gallery(preview_modules):
    _, cog_module, Context, Member, Channel = preview_modules
    _, gen, _, _ = make_job(cog_module, Context, Member, Channel, nsfw=True)
    file = discord.File(BytesIO(b"preview"), filename="preview.jpg", spoiler=True)
    gen.progress_v2_view.set_progress("Generating image...", "`69%`", "`40s`", "`16/24`", file.filename)

    params = handle_message_parameters(view=gen.progress_v2_view, attachments=[file])
    payload = json.loads(params.multipart[0]["value"])
    container, = payload["components"]
    galleries = v2_gallery(container["components"])

    assert payload["flags"] & (1 << 15)
    assert payload["attachments"] == [{"id": 0, "filename": "SPOILER_preview.jpg"}]
    assert len(galleries) == 1
    assert galleries[0]["items"] == [{
        "media": {"url": "attachment://SPOILER_preview.jpg"}, "spoiler": True,
    }]


@pytest.mark.asyncio
async def test_v2_cancel_removes_gallery_and_buttons(preview_modules, monkeypatch):
    _, cog_module, Context, Member, Channel = preview_modules
    cog, gen, _, _ = make_job(cog_module, Context, Member, Channel, nsfw=True)
    gen.user.id = 1
    gen.user.mention = "<@1>"
    gen.progress_v2_view.set_progress("Generating image...", "`69%`", preview_filename="SPOILER_preview.jpg")
    calls = []

    async def cancel_request(job_id):
        calls.append(("cancel", job_id))

    async def no_sleep(_seconds):
        pass

    class Message:
        async def edit(self, **kwargs):
            calls.append(("edit", kwargs))

        async def delete(self):
            calls.append("delete")

    interaction = SimpleNamespace(
        user=gen.user,
        guild=SimpleNamespace(get_member=lambda _id: gen.user),
        channel=SimpleNamespace(permissions_for=lambda _member: SimpleNamespace(manage_messages=True)),
        message=Message(),
    )
    cog.api = SimpleNamespace(cancel_request=cancel_request)
    monkeypatch.setattr(sys.modules["arcenciel.views.generating"].asyncio, "sleep", no_sleep)

    await gen.progress_v2_view.cancel(interaction)

    assert gen.cancelled is True
    assert gen.id not in cog.queued_images
    assert calls[0] == ("cancel", gen.id)
    assert calls[1][0] == "edit"
    assert calls[1][1]["attachments"] == []
    assert calls[1][1]["view"].to_components()[0]["components"] == [{
        "type": 10, "content": "❌ Request cancelled."
    }]
    assert calls[2] == "delete"


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
    assert first["attachments"][0].spoiler is True
    assert first["attachments"][0].filename.startswith("SPOILER_")
    first_children = v2_children(first)
    assert "**Preview step** `4/24`" in v2_text(first_children)
    assert "**ETA** <t:" in v2_text(first_children)
    assert v2_gallery(first_children)[0]["items"] == [{
        "media": {"url": f"attachment://{gen.preview_filename}"}, "spoiler": True,
    }]
    assert first_children[-1]["type"] == 1

    gen.last_updated -= timedelta(seconds=10)
    job["progress"]["percent"] = 27
    await cog.update_job(job, gen)

    second = message.edits[-1]
    assert "attachments" not in second
    second_children = v2_children(second)
    assert "**Progress** `27%`" in v2_text(second_children)
    assert len(v2_gallery(second_children)) == 1

    gen.channel.nsfw = False
    gen.last_updated -= timedelta(seconds=10)
    await cog.update_job(job, gen)
    third = message.edits[-1]
    assert third["attachments"] == []
    third_children = v2_children(third)
    assert not v2_gallery(third_children)
    assert "**Progress** `27%`" in v2_text(third_children)


@pytest.mark.asyncio
async def test_new_preview_replaces_old_spoiler_and_keeps_progress(preview_modules):
    _, cog_module, Context, Member, Channel = preview_modules
    cog, gen, job, message = make_job(cog_module, Context, Member, Channel, nsfw=True)
    gen.pending_preview = (b"first", "image/jpeg", 4, 24)
    gen.preview_version = 1
    await cog.update_job(job, gen)
    first_name = message.edits[-1]["attachments"][0].filename

    gen.last_updated -= timedelta(seconds=10)
    gen.pending_preview = (b"second", "image/jpeg", 8, 24)
    gen.preview_version = 2
    job["progress"]["percent"] = 35
    await cog.update_job(job, gen)

    edit = message.edits[-1]
    assert len(edit["attachments"]) == 1
    assert edit["attachments"][0].spoiler is True
    assert edit["attachments"][0].filename != first_name
    children = v2_children(edit)
    assert "**Progress** `35%`" in v2_text(children)
    assert "**Preview step** `8/24`" in v2_text(children)
    assert len(v2_gallery(children)) == 1
    assert v2_gallery(children)[0]["items"][0]["media"]["url"] == f"attachment://{edit['attachments'][0].filename}"


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
    children = v2_children(message.edits[-1])
    assert "**Progress** `23%`" in v2_text(children)
    assert not v2_gallery(children)


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
