#!/usr/bin/env python3
"""Discord slash command that sends image + mask to Retouch's cloud service."""
import asyncio
import io
import logging
import os

import discord
from discord import app_commands
from PIL import Image, ImageDraw

from cloud import MAX_BYTES, RetouchClient, ServiceError, decode_image, encode_image

log = logging.getLogger("retouch")


class RetouchBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.service = RetouchClient(os.environ["RETOUCH_PROFILE"])
        self.busy = asyncio.Lock()
        self.allowed = {int(value) for value in os.environ.get("DISCORD_ALLOWED_USERS", "").split(",") if value.strip()}
        self.tree.add_command(retouch)
        self.tree.on_error = self.command_error

    async def setup_hook(self):
        guild_id = os.environ.get("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self):
        self.service.close()
        await super().close()

    async def on_ready(self):
        log.info("Discord connected; Retouch cloud adapter enabled")

    async def command_error(self, interaction, error):
        message = "تعذر تنفيذ الأمر. تحقق من المدخلات أو انتظر قليلًا وأعد المحاولة."
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"انتظر {error.retry_after:.0f} ثانية قبل الطلب التالي."
        sender = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        await sender(message, ephemeral=True)


@app_commands.command(name="retouch", description="حذف منطقة نص عبر خادم Retouch دون تشغيل نموذج محلي")
@app_commands.guild_only()
@app_commands.checks.cooldown(1, 15, key=lambda interaction: (interaction.guild_id, interaction.user.id))
@app_commands.describe(image="صورة PNG/JPEG/WebP", mask="قناع بنفس الأبعاد: الأبيض للحذف والأسود للحفظ",
                       x="بداية المستطيل أفقيًا", y="بداية المستطيل رأسيًا", width="عرض منطقة النص", height="ارتفاع منطقة النص",
                       mode="AI أو الحذف العادي؛ يطبق الخادم رصيد حسابك")
@app_commands.choices(mode=[app_commands.Choice(name="AI", value="ai"), app_commands.Choice(name="عادي", value="standard")])
async def retouch(interaction: discord.Interaction, image: discord.Attachment,
                  mask: discord.Attachment | None = None,
                  x: int = 0, y: int = 0, width: int = 0, height: int = 0, mode: str = "ai"):
    client = interaction.client
    if client.allowed and interaction.user.id not in client.allowed:
        await interaction.response.send_message("هذا البوت مخصص للمستخدمين المحددين في إعداداته.", ephemeral=True)
        return
    if image.size > MAX_BYTES or (mask and mask.size > MAX_BYTES):
        await interaction.response.send_message("الحد الأقصى لكل مرفق هو 20 MB.", ephemeral=True)
        return
    if client.busy.locked():
        await interaction.response.send_message("هناك صورة قيد المعالجة؛ أعد المحاولة بعد اكتمالها.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        async with client.busy:
            source = await image.read()
            if mask:
                selection = await mask.read()
            else:
                original = await asyncio.to_thread(decode_image, source)
                if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > original.width or y + height > original.height:
                    raise ValueError("أرفق قناعًا أو حدد x وy وwidth وheight داخل أبعاد الصورة")
                region = Image.new("L", original.size, 0)
                ImageDraw.Draw(region).rectangle((x, y, x + width - 1, y + height - 1), fill=255)
                selection = encode_image(region, "PNG")
            result = await asyncio.to_thread(client.service.clean, source, selection, mode=mode)
            upload_limit = interaction.guild.filesize_limit if interaction.guild else 8 * 1024 * 1024
            if len(result) > upload_limit:
                raise ValueError("النتيجة أكبر من حد مرفقات هذا الخادم؛ أرسل قصاصة أصغر")
            await interaction.followup.send("تمت المعالجة عبر خادم Retouch.", file=discord.File(io.BytesIO(result), filename="retouch-result.png"), ephemeral=True)
    except (ValueError, ServiceError, OSError) as error:
        await interaction.followup.send(str(error)[:400], ephemeral=True)
    except Exception:
        log.error("Retouch request failed (%s)", interaction.id)
        await interaction.followup.send("فشلت المعالجة؛ لم تُستخدم نتيجة بديلة أو نموذج محلي.", ephemeral=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for name in ("DISCORD_BOT_TOKEN", "RETOUCH_PROFILE"):
        if not os.environ.get(name):
            raise SystemExit(f"Missing environment variable: {name}")
    RetouchBot().run(os.environ["DISCORD_BOT_TOKEN"], log_handler=None)
