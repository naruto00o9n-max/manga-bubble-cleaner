"""Send one image to /clean; text detection and masking are automatic."""
import asyncio
import io
import logging
import os

import discord
from discord import app_commands

from free_cloud import AutomaticCleaner, FreeCloudError


class MangaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(clean)
        self.cleaner = AutomaticCleaner()
        self.busy = asyncio.Lock()
        self.allowed = {int(item) for item in os.getenv("DISCORD_ALLOWED_USERS", "").split(",") if item.strip()}
        self.tree.on_error = self.command_error

    async def setup_hook(self):
        if guild_id := os.getenv("DISCORD_GUILD_ID"):
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def command_error(self, interaction, error):
        message = "تعذر تنفيذ الأمر؛ حاول مجددًا بعد قليل."
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"انتظر {error.retry_after:.0f} ثانية قبل الصورة التالية."
        sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await sender(message, ephemeral=True)


@app_commands.command(name="clean", description="أرسل صورة المانهوا لحذف النصوص المكتشفة وترميم خلفياتها تلقائيًا")
@app_commands.guild_only()
@app_commands.checks.cooldown(1, 30, key=lambda interaction: interaction.user.id)
@app_commands.describe(image="صورة المانهوا بصيغة PNG أو JPEG أو WebP")
async def clean(interaction: discord.Interaction, image: discord.Attachment):
    bot = interaction.client
    if bot.allowed and interaction.user.id not in bot.allowed:
        await interaction.response.send_message("هذا البوت مخصص للمستخدمين المحددين في إعداداته.", ephemeral=True)
        return
    if image.size > 20 * 1024 * 1024:
        await interaction.response.send_message("أرسل صورة أصغر من 20 MB.", ephemeral=True)
        return
    if bot.busy.locked():
        await interaction.response.send_message("هناك صورة قيد المعالجة؛ انتظر اكتمالها.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    async with bot.busy:
        try:
            data = await image.read()
            result, _, info = await asyncio.to_thread(bot.cleaner.clean, data)
            if len(result) > interaction.guild.filesize_limit:
                raise ValueError("النتيجة أكبر من حد المرفقات في خادم Discord؛ أرسل لوحة أصغر")
            await interaction.followup.send(
                "أرفقت الصورة بعد حذف النصوص التي اكتشفها البوت. راجع التفاصيل الدقيقة قبل استخدامها.",
                file=discord.File(io.BytesIO(result), filename="cleaned.png"), ephemeral=True)
        except (ValueError, OSError, FreeCloudError) as error:
            await interaction.followup.send(str(error)[:300], ephemeral=True)
        except Exception as error:
            logging.error("Automatic cleanup failed: %s", type(error).__name__)
            await interaction.followup.send("تعذرت معالجة الصورة. لم تُرجع نتيجة غير مكتملة.", ephemeral=True)


if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing DISCORD_BOT_TOKEN")
    logging.basicConfig(level=logging.INFO)
    MangaBot().run(token, log_handler=None)
