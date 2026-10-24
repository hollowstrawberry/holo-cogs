from collections.abc import Awaitable, Callable

import discord
from agent.constants import VIEW_TIMEOUT
from agent.views.prompt_store import OwnerCheck, PromptRef, PromptStore, display_name, prompt_stats


class PromptEditModal(discord.ui.Modal):
    def __init__(
        self,
        store: PromptStore,
        ref: PromptRef | None,
        check_owner: OwnerCheck,
        is_active: Callable[[], bool],
        on_saved: Callable[[], Awaitable[None]],
    ):
        super().__init__(title="Prompt editing" if ref else "New prompt key", timeout=VIEW_TIMEOUT)
        self.store = store
        self.ref = ref
        self.check_owner = check_owner
        self.is_active = is_active
        self.on_saved = on_saved
        self.expected = store.get(ref) if ref else None
        self.name_input: discord.ui.TextInput[PromptEditModal] = discord.ui.TextInput(
            min_length=1,
            max_length=100,
        )
        self.prompt_input: discord.ui.TextInput[PromptEditModal] = discord.ui.TextInput(
            style=discord.TextStyle.long,
            default=self.expected or "",
            required=False,
            max_length=4000,
        )
        if ref is None:
            self.add_item(discord.ui.Label(text="Prompt key name", component=self.name_input))
        self.add_item(
            discord.ui.Label(
                text=ref.name[:45] if ref else "Prompt key value",
                component=self.prompt_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.check_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
            return
        if not self.is_active():
            await interaction.response.send_message(
                "This panel has expired. Run the prompt command again.", ephemeral=True
            )
            return
        ref = self.ref
        if ref is None:
            name = self.name_input.value.replace("`", "").strip()
            if not name or len(name) > 100:
                await interaction.response.send_message("Key names must contain 1–100 characters.", ephemeral=True)
                return
            ref = PromptRef("key", name)
        prompt = self.prompt_input.value
        if len(prompt) > 4000:
            await interaction.response.send_message("The editor supports up to 4,000 characters.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.store.save(ref, prompt, self.expected)
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        verb = "updated" if self.ref else "created"
        await interaction.followup.send(
            f"**{display_name(ref.name)}** {verb}.\n{prompt_stats(prompt)}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.on_saved()
