import io

import discord
from agent.views.prompt_edit_modal import PromptEditModal
from agent.views.prompt_store import (
    OwnerCheck,
    PromptButton,
    PromptMessageView,
    PromptRef,
    PromptStore,
    display_name,
    prompt_stats,
)


class PromptView(PromptMessageView):
    def __init__(
        self,
        store: PromptStore,
        ref: PromptRef,
        check_owner: OwnerCheck,
        color: discord.Color,
        *,
        private: bool = False,
    ):
        super().__init__()
        self.store, self.ref, self.check_owner = store, ref, check_owner
        self.color, self.private = color, private
        self.render()

    def render(self) -> None:
        self.clear_items()
        container: discord.ui.Container[PromptView] = discord.ui.Container(accent_color=self.color)
        category = "Module" if self.ref.kind == "module" else "Prompt key"
        container.add_item(discord.ui.TextDisplay(f"## {category}: {display_name(self.ref.name)}"))
        prompt = self.store.get(self.ref)
        if self.private:
            if prompt is None:
                container.add_item(discord.ui.TextDisplay("This prompt no longer exists."))
            else:
                container.add_item(discord.ui.TextDisplay(prompt_stats(prompt)))
                preview = prompt[:2500].replace("```", "``\u200b`")
                container.add_item(discord.ui.TextDisplay(f"```\n{preview}\n```" if prompt else "*Empty prompt.*"))
                if len(prompt) > 2500:
                    container.add_item(
                        discord.ui.TextDisplay("Preview shortened. Download TXT for the complete prompt.")
                    )
        else:
            container.add_item(discord.ui.TextDisplay("Open a private preview to view this prompt."))
        controls: discord.ui.ActionRow[PromptView] = discord.ui.ActionRow()
        controls.add_item(PromptButton("Refresh" if self.private else "View privately", self.show_prompt))
        controls.add_item(PromptButton("Edit", self.edit_prompt, disabled=prompt is None))
        controls.add_item(PromptButton("Download TXT", self.download, disabled=prompt is None))
        container.add_item(controls)
        self.add_item(container)

    async def show_prompt(self, interaction: discord.Interaction) -> None:
        if self.private:
            self.render()
            await interaction.response.edit_message(view=self, allowed_mentions=discord.AllowedMentions.none())
            return
        await self.send_private(interaction, self.store, self.ref, self.check_owner, self.color)

    @classmethod
    async def send_private(
        cls,
        interaction: discord.Interaction,
        store: PromptStore,
        ref: PromptRef,
        check_owner: OwnerCheck,
        color: discord.Color,
    ) -> None:
        view = cls(store, ref, check_owner, color, private=True)
        await interaction.response.send_message(
            view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )
        view.message = await interaction.original_response()

    async def edit_prompt(self, interaction: discord.Interaction) -> None:
        if not await self.check_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can edit prompts.", ephemeral=True)
            return
        prompt = self.store.get(self.ref)
        if prompt is None:
            await interaction.response.send_message("This prompt no longer exists. Refresh the panel.", ephemeral=True)
            return
        if len(prompt) > 4000:
            await interaction.response.send_message(
                "This prompt exceeds the editor's 4,000-character limit. Download TXT to read it in full; "
                "it has not been truncated or changed.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            PromptEditModal(
                self.store,
                self.ref,
                self.check_owner,
                lambda: not self.is_finished(),
                self.refresh,
            )
        )

    async def download(self, interaction: discord.Interaction) -> None:
        prompt = self.store.get(self.ref)
        if prompt is None:
            await interaction.response.send_message("This prompt no longer exists.", ephemeral=True)
            return
        file = discord.File(io.BytesIO(prompt.encode("utf-8")), filename="prompt.txt")
        await interaction.response.send_message(
            file=file, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    async def refresh(self) -> None:
        if self.message and not self.is_finished():
            self.render()
            try:
                await self.message.edit(view=self, allowed_mentions=discord.AllowedMentions.none())
            except (discord.NotFound, discord.Forbidden):
                self.stop()
