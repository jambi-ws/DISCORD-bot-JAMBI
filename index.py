import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os
import sqlite3
import time
import datetime
import traceback
import re

# ---------- 버전 정보 ----------
BOT_VERSION = "1.3.1"

# ---------- 기본 설정 ----------
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(dotenv_path)
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- 데이터베이스 ----------
conn = sqlite3.connect("points.db")
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS points (
    user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)""")

cur.execute("PRAGMA table_info(bets)")
existing_columns = [row[1] for row in cur.fetchall()]

if not existing_columns:
    cur.execute("""CREATE TABLE bets (
        bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        option_a TEXT,
        option_b TEXT,
        status TEXT DEFAULT 'open',
        channel_id INTEGER,
        message_id INTEGER,
        created_at INTEGER,
        creator_id INTEGER,
        duration_seconds INTEGER DEFAULT 180)""")
else:
    if "option_a" not in existing_columns:
        cur.execute("ALTER TABLE bets ADD COLUMN option_a TEXT DEFAULT '성공'")
    if "option_b" not in existing_columns:
        cur.execute("ALTER TABLE bets ADD COLUMN option_b TEXT DEFAULT '실패'")
    if "created_at" not in existing_columns:
        cur.execute("ALTER TABLE bets ADD COLUMN created_at INTEGER DEFAULT 0")
    if "creator_id" not in existing_columns:
        cur.execute("ALTER TABLE bets ADD COLUMN creator_id INTEGER DEFAULT 0")
    if "duration_seconds" not in existing_columns:
        cur.execute("ALTER TABLE bets ADD COLUMN duration_seconds INTEGER DEFAULT 180")

cur.execute("""CREATE TABLE IF NOT EXISTS wagers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_id INTEGER,
    user_id INTEGER,
    choice TEXT,
    amount INTEGER)""")

cur.execute("""CREATE TABLE IF NOT EXISTS temp_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_type TEXT,
    guild_id INTEGER,
    target_user_id INTEGER,
    original_name TEXT,
    original_icon BLOB,
    original_nick TEXT,
    had_nick INTEGER,
    expires_at INTEGER)""")
conn.commit()

DURATION_OPTIONS = [
    ("15초", 15),
    ("30초", 30),
    ("1분", 60),
    ("3분", 180),
]

# ---------- 만두집 상점 설정 ----------
TEXT_HIGHLIGHT_COST = 100
SERVER_DECOR_COST = 2000
NICKNAME_CHANGE_COST = 800
TEMP_CHANGE_DURATION = 60  # 1분 (테스트용, 원래는 86400=24시간)

def get_points(user_id):
    cur.execute("SELECT points FROM points WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO points (user_id, points) VALUES (?, 0)", (user_id,))
        conn.commit()
        return 0
    return row[0]

def add_points(user_id, amount):
    get_points(user_id)
    cur.execute("UPDATE points SET points = points + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def get_rank(user_id):
    cur.execute("SELECT user_id FROM points ORDER BY points DESC")
    rows = cur.fetchall()
    for i, (uid,) in enumerate(rows, start=1):
        if uid == user_id:
            return i, len(rows)
    return None, len(rows)

# ---------- 버전 확인 ----------
@bot.command(name="버전확인")
async def 버전확인(ctx):
    await ctx.send(f"🤖 현재 봇 버전: **v{BOT_VERSION}**")

# ---------- 음성 채널 만두 지급 ----------
voice_tracker = {}

@tasks.loop(seconds=60)
async def voice_point_task():
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if member.bot:
                    continue
                uid = member.id
                voice_tracker[uid] = voice_tracker.get(uid, 0) + 60
                elapsed = voice_tracker[uid]
                if elapsed % 600 == 0:
                    add_points(uid, 20)
                if elapsed % 3600 == 0:
                    add_points(uid, 10)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if after.channel is None:
        voice_tracker.pop(member.id, None)

# ---------- 만두 확인 / 전체 (슬래시 명령어) ----------
@bot.tree.command(name="만두확인", description="내 만두 개수와 순위를 확인합니다 (나만 볼 수 있음)")
async def 만두확인(interaction: discord.Interaction):
    pts = get_points(interaction.user.id)
    rank, total = get_rank(interaction.user.id)
    msg = f"🥟 {interaction.user.mention}님의 만두: **{pts}개**\n순위: **{rank}위** / 총 {total}명"
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="만두전체", description="만두 상위 10명을 확인합니다 (나만 볼 수 있음)")
async def 만두전체(interaction: discord.Interaction):
    cur.execute("SELECT user_id, points FROM points ORDER BY points DESC LIMIT 10")
    rows = cur.fetchall()
    if not rows:
        await interaction.response.send_message("아직 기록된 만두가 없습니다.", ephemeral=True)
        return
    lines = []
    for i, (uid, pts) in enumerate(rows, start=1):
        member = interaction.guild.get_member(uid)
        name = member.display_name if member else f"알 수 없음({uid})"
        lines.append(f"{i}. {name} - {pts}개")
    embed = discord.Embed(title="🥟 만두 상위 10위", description="\n".join(lines), color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------- 만두 지급 (! 명령어, 관리자 전용) ----------
@bot.group(invoke_without_command=True)
async def 만두(ctx):
    await ctx.send("사용법: `!만두 지급 @사용자 개수` (관리자 전용)")

@만두.command(name="지급")
@commands.has_permissions(administrator=True)
async def 만두_지급(ctx, 대상: discord.Member, 개수: int):
    add_points(대상.id, 개수)
    new_total = get_points(대상.id)
    await ctx.send(f"{대상.mention}님에게 만두 **{개수}개**를 지급했습니다. (현재 보유: {new_total}개) 🥟")

@만두_지급.error
async def 만두_지급_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("이 명령어는 관리자만 사용할 수 있습니다.")
    elif isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
        await ctx.send("사용법: `!만두 지급 @사용자 개수` (예: `!만두 지급 @홍길동 50`)")

# ================= 만두집 상점 =================

def schedule_temp_change_revert(change_id, expires_at):
    async def revert_task():
        delay = expires_at - int(time.time())
        if delay > 0:
            await discord.utils.sleep_until(discord.utils.utcnow() + datetime.timedelta(seconds=delay))
        await apply_revert(change_id)
    bot.loop.create_task(revert_task())

async def apply_revert(change_id):
    cur.execute("SELECT change_type, guild_id, target_user_id, original_name, original_icon, original_nick, had_nick FROM temp_changes WHERE id=?", (change_id,))
    row = cur.fetchone()
    if not row:
        return
    change_type, guild_id, target_user_id, original_name, original_icon, original_nick, had_nick = row

    guild = bot.get_guild(guild_id)
    if guild is None:
        cur.execute("DELETE FROM temp_changes WHERE id=?", (change_id,))
        conn.commit()
        return

    try:
        if change_type == "server":
            await guild.edit(name=original_name, icon=original_icon, reason="만두집: 서버 프로필 꾸미기 기간 만료 - 원상복구")
        elif change_type == "nickname":
            member = guild.get_member(target_user_id)
            if member:
                restore_nick = original_nick if had_nick else None
                await member.edit(nick=restore_nick, reason="만두집: 닉네임 교체권 기간 만료 - 원상복구")
    except discord.HTTPException as e:
        print(f"[만두집 복구 실패] change_id={change_id}, error={e}")

    cur.execute("DELETE FROM temp_changes WHERE id=?", (change_id,))
    conn.commit()

def restore_pending_temp_changes():
    cur.execute("SELECT id, expires_at FROM temp_changes")
    rows = cur.fetchall()
    now = int(time.time())
    for change_id, expires_at in rows:
        if expires_at <= now:
            bot.loop.create_task(apply_revert(change_id))
        else:
            schedule_temp_change_revert(change_id, expires_at)

# ---------- /만두집 메인 메뉴 ----------
class ShopMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label=f"✨ 텍스트 강조 ({TEXT_HIGHLIGHT_COST}개)", style=discord.ButtonStyle.blurple)
    async def highlight_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pts = get_points(interaction.user.id)
        if pts < TEXT_HIGHLIGHT_COST:
            await interaction.response.send_message(f"만두가 부족합니다. (필요: {TEXT_HIGHLIGHT_COST}개 / 보유: {pts}개)", ephemeral=True)
            return
        await interaction.response.send_modal(HighlightModal())

    @discord.ui.button(label=f"🖼️ 서버 프로필 꾸미기 ({SERVER_DECOR_COST}개)", style=discord.ButtonStyle.blurple)
    async def server_decor_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pts = get_points(interaction.user.id)
        if pts < SERVER_DECOR_COST:
            await interaction.response.send_message(f"만두가 부족합니다. (필요: {SERVER_DECOR_COST}개 / 보유: {pts}개)", ephemeral=True)
            return
        if not interaction.guild.me.guild_permissions.manage_guild:
            await interaction.response.send_message("⚠️ 봇에게 '서버 관리' 권한이 없어서 사용할 수 없습니다. 관리자에게 문의해주세요.", ephemeral=True)
            return
        await interaction.response.send_modal(ServerDecorModal())

    @discord.ui.button(label=f"🏷️ 타인 닉네임 1일 교체권 ({NICKNAME_CHANGE_COST}개)", style=discord.ButtonStyle.blurple)
    async def nickname_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pts = get_points(interaction.user.id)
        if pts < NICKNAME_CHANGE_COST:
            await interaction.response.send_message(f"만두가 부족합니다. (필요: {NICKNAME_CHANGE_COST}개 / 보유: {pts}개)", ephemeral=True)
            return
        if not interaction.guild.me.guild_permissions.manage_nicknames:
            await interaction.response.send_message("⚠️ 봇에게 '닉네임 관리' 권한이 없어서 사용할 수 없습니다. 관리자에게 문의해주세요.", ephemeral=True)
            return
        await interaction.response.send_modal(NicknameChangeModal())

@bot.tree.command(name="만두집", description="만두로 할 수 있는 것들을 확인합니다 (나만 볼 수 있음)")
async def 만두집(interaction: discord.Interaction):
    pts = get_points(interaction.user.id)
    embed = discord.Embed(
        title="🥟 만두집 상점",
        description=f"현재 보유 만두: **{pts}개**\n\n아래 버튼을 눌러 만두를 사용해보세요!",
        color=discord.Color.orange()
    )
    embed.add_field(name="✨ 텍스트 강조", value=f"{TEXT_HIGHLIGHT_COST}개 · 원하는 문구를 화려하게 강조해서 채팅에 게시", inline=False)
    embed.add_field(name="🖼️ 서버 프로필 꾸미기", value=f"{SERVER_DECOR_COST}개 · {TEMP_CHANGE_DURATION}초 동안 서버 이름/아이콘 변경 (이후 자동 복구)", inline=False)
    embed.add_field(name="🏷️ 타인 닉네임 1일 교체권", value=f"{NICKNAME_CHANGE_COST}개 · 상대 닉네임을 {TEMP_CHANGE_DURATION}초 동안 변경 (이후 자동 복구)", inline=False)
    await interaction.response.send_message(embed=embed, view=ShopMainView(), ephemeral=True)

# ---------- 1. 텍스트 강조 ----------
class HighlightModal(discord.ui.Modal, title="✨ 텍스트 강조"):
    text_input = discord.ui.TextInput(
        label="강조하고 싶은 문구를 입력하세요",
        placeholder="예: 오늘 방송 대박!",
        max_length=100,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        pts = get_points(interaction.user.id)
        if pts < TEXT_HIGHLIGHT_COST:
            await interaction.response.send_message(f"만두가 부족합니다. (필요: {TEXT_HIGHLIGHT_COST}개 / 보유: {pts}개)", ephemeral=True)
            return

        add_points(interaction.user.id, -TEXT_HIGHLIGHT_COST)

        text = self.text_input.value
        embed = discord.Embed(
            description=f"# 🌟 {text} 🌟",
            color=discord.Color.gold()
        )
        embed.set_author(name=f"{interaction.user.display_name}님의 강조 메시지", icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text="✨ 만두 " + str(TEXT_HIGHLIGHT_COST) + "개로 강조된 메시지입니다 ✨")

        await interaction.response.send_message("강조 메시지를 게시했습니다! 🎉", ephemeral=True)
        await interaction.channel.send(content="🎉🎊✨━━━━━━━━━━━━━━━━━━━✨🎊🎉", embed=embed)

# ---------- 2. 서버 프로필 꾸미기 ----------
class ServerDecorModal(discord.ui.Modal, title="🖼️ 서버 프로필 꾸미기"):
    new_name = discord.ui.TextInput(
        label="새 서버 이름",
        placeholder="예: 오늘은 만두데이",
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"좋습니다! 이제 **이 채널에 아이콘으로 쓸 이미지 파일을 첨부**해서 아무 메시지나 보내주세요. (2분 이내, 이미지 하나만)",
            ephemeral=True
        )

        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel_id and len(m.attachments) > 0

        try:
            msg = await bot.wait_for("message", check=check, timeout=120)
        except Exception:
            await interaction.followup.send("⏰ 시간이 초과되어 취소되었습니다. 만두는 차감되지 않았습니다.", ephemeral=True)
            return

        attachment = msg.attachments[0]
        if not attachment.content_type or "image" not in attachment.content_type:
            await interaction.followup.send("이미지 파일이 아닙니다. 처음부터 다시 시도해주세요. 만두는 차감되지 않았습니다.", ephemeral=True)
            return

        pts = get_points(interaction.user.id)
        if pts < SERVER_DECOR_COST:
            await interaction.followup.send(f"만두가 부족합니다. (필요: {SERVER_DECOR_COST}개 / 보유: {pts}개)", ephemeral=True)
            return

        guild = interaction.guild
        try:
            original_icon_bytes = await guild.icon.read() if guild.icon else None
        except Exception:
            original_icon_bytes = None
        original_name = guild.name

        new_icon_bytes = await attachment.read()

        try:
            await guild.edit(name=self.new_name.value, icon=new_icon_bytes, reason=f"{interaction.user}님이 만두집 - 서버 프로필 꾸미기 사용")
        except discord.HTTPException as e:
            await interaction.followup.send(f"⚠️ 서버 프로필 변경에 실패했습니다: {e}", ephemeral=True)
            return

        add_points(interaction.user.id, -SERVER_DECOR_COST)
        expires_at = int(time.time()) + TEMP_CHANGE_DURATION

        cur.execute(
            "INSERT INTO temp_changes (change_type, guild_id, target_user_id, original_name, original_icon, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("server", guild.id, None, original_name, original_icon_bytes, expires_at)
        )
        conn.commit()
        change_id = cur.lastrowid
        schedule_temp_change_revert(change_id, expires_at)

        try:
            await msg.delete()
        except discord.HTTPException:
            pass

        await interaction.followup.send(
            f"✅ 서버 프로필이 변경되었습니다! {TEMP_CHANGE_DURATION}초 후 자동으로 원래대로 복구됩니다.",
            ephemeral=True
        )
        await interaction.channel.send(f"🖼️ {interaction.user.mention}님이 만두 {SERVER_DECOR_COST}개로 서버 프로필을 꾸몄습니다! ({TEMP_CHANGE_DURATION}초 후 복구)")

# ---------- 3. 타인 닉네임 1일 교체권 ----------
class NicknameChangeModal(discord.ui.Modal, title="🏷️ 타인 닉네임 1일 교체권"):
    target_input = discord.ui.TextInput(
        label="대상 (@멘션 또는 사용자 ID)",
        placeholder="예: @홍길동 또는 123456789012345678"
    )
    new_nick_input = discord.ui.TextInput(
        label="바꿀 닉네임",
        placeholder="예: 오늘의 주인공",
        max_length=32
    )

    async def on_submit(self, interaction: discord.Interaction):
        pts = get_points(interaction.user.id)
        if pts < NICKNAME_CHANGE_COST:
            await interaction.response.send_message(f"만두가 부족합니다. (필요: {NICKNAME_CHANGE_COST}개 / 보유: {pts}개)", ephemeral=True)
            return

        raw = self.target_input.value.strip()
        match = re.search(r"(\d{15,20})", raw)
        if not match:
            await interaction.response.send_message("대상을 인식할 수 없습니다. @멘션 또는 숫자로 된 사용자 ID를 입력해주세요.", ephemeral=True)
            return

        target_id = int(match.group(1))
        target = interaction.guild.get_member(target_id)
        if target is None:
            await interaction.response.send_message("해당 사용자를 서버에서 찾을 수 없습니다.", ephemeral=True)
            return

        if target.id == interaction.guild.owner_id:
            await interaction.response.send_message("서버 소유자의 닉네임은 변경할 수 없습니다.", ephemeral=True)
            return

        if target.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message("봇보다 역할이 높거나 같은 사용자의 닉네임은 변경할 수 없습니다.", ephemeral=True)
            return

        original_nick = target.nick
        had_nick = 1 if original_nick is not None else 0

        try:
            await target.edit(nick=self.new_nick_input.value, reason=f"{interaction.user}님이 만두집 - 닉네임 교체권 사용")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"⚠️ 닉네임 변경에 실패했습니다: {e}", ephemeral=True)
            return

        add_points(interaction.user.id, -NICKNAME_CHANGE_COST)
        expires_at = int(time.time()) + TEMP_CHANGE_DURATION

        cur.execute(
            "INSERT INTO temp_changes (change_type, guild_id, target_user_id, original_nick, had_nick, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("nickname", interaction.guild.id, target.id, original_nick, had_nick, expires_at)
        )
        conn.commit()
        change_id = cur.lastrowid
        schedule_temp_change_revert(change_id, expires_at)

        await interaction.response.send_message(
            f"✅ {target.mention}님의 닉네임을 **'{self.new_nick_input.value}'**(으)로 변경했습니다! {TEMP_CHANGE_DURATION}초 후 자동으로 원래대로 복구됩니다.",
            ephemeral=True
        )
        await interaction.channel.send(f"🏷️ {interaction.user.mention}님이 만두 {NICKNAME_CHANGE_COST}개로 {target.mention}님의 닉네임을 바꿨습니다! ({TEMP_CHANGE_DURATION}초 후 복구)")

# ================= 승부예측 시스템 =================
async def get_bet_stats(bet_id):
    cur.execute("SELECT choice, COUNT(*), SUM(amount) FROM wagers WHERE bet_id=? GROUP BY choice", (bet_id,))
    stats = {"a": {"count": 0, "total": 0}, "b": {"count": 0, "total": 0}}
    for choice, count, total in cur.fetchall():
        stats[choice] = {"count": count, "total": total or 0}
    return stats

def build_bet_embed(title, option_a, option_b, stats, status, created_at, duration_seconds):
    embed = discord.Embed(title=f"🔮 승부예측: {title}", color=discord.Color.blurple())
    embed.add_field(
        name=f"✅ {option_a}",
        value=f"{stats['a']['count']}명 참여 / 총 {stats['a']['total']}개",
        inline=True
    )
    embed.add_field(
        name=f"❌ {option_b}",
        value=f"{stats['b']['count']}명 참여 / 총 {stats['b']['total']}개",
        inline=True
    )
    if status == "open":
        remaining = duration_seconds - (int(time.time()) - created_at)
        if remaining > 0:
            embed.set_footer(text=f"상태: 진행중 (배팅 마감까지 약 {remaining // 60}분 {remaining % 60}초) · 같은 옵션에는 추가 배팅 가능")
        else:
            embed.set_footer(text="상태: 배팅 마감됨 · 결과 발표 대기중")
    elif status == "cancelled":
        embed.set_footer(text="상태: 취소됨 (전액 환불)")
    else:
        winner_name = option_a if status == "a" else option_b
        embed.set_footer(text=f"상태: 종료 (승리: {winner_name})")
    return embed

async def refresh_bet_message(bet_id, view=None):
    cur.execute("SELECT title, option_a, option_b, status, channel_id, message_id, created_at, duration_seconds FROM bets WHERE bet_id=?", (bet_id,))
    row = cur.fetchone()
    if not row:
        return
    title, option_a, option_b, status, channel_id, message_id, created_at, duration_seconds = row
    stats = await get_bet_stats(bet_id)
    embed = build_bet_embed(title, option_a, option_b, stats, status, created_at, duration_seconds)

    channel = bot.get_channel(channel_id)
    if channel and message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed, view=view)
        except discord.NotFound:
            pass

class BetModal(discord.ui.Modal):
    def __init__(self, bet_id, choice, option_a_label, option_b_label, current_points, extra_mode=False):
        label = option_a_label if choice == "a" else option_b_label
        title_text = f"'{label}'에 추가 배팅하기" if extra_mode else f"'{label}'에 배팅하기"
        super().__init__(title=title_text)
        self.bet_id = bet_id
        self.choice = choice
        self.option_a_label = option_a_label
        self.option_b_label = option_b_label
        self.amount_input = discord.ui.TextInput(
            label=f"배팅할 만두 수 (보유 {current_points}개)",
            placeholder=f"현재 보유: {current_points}개 · 숫자만 입력",
            max_length=10
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        cur.execute("SELECT status, created_at, duration_seconds FROM bets WHERE bet_id=?", (self.bet_id,))
        row = cur.fetchone()
        if not row or row[0] != "open":
            await interaction.response.send_message("이 승부예측은 더 이상 참여할 수 없습니다.", ephemeral=True)
            return
        status, created_at, duration_seconds = row
        if int(time.time()) - created_at > duration_seconds:
            await interaction.response.send_message("배팅 가능 시간이 지났습니다.", ephemeral=True)
            return

        try:
            amount = int(self.amount_input.value)
        except ValueError:
            await interaction.response.send_message("숫자만 입력해주세요.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("1 이상의 값을 입력해주세요.", ephemeral=True)
            return

        user_points = get_points(interaction.user.id)
        if amount > user_points:
            await interaction.response.send_message(f"만두가 부족합니다. (보유: {user_points}개)", ephemeral=True)
            return

        cur.execute("SELECT choice, amount FROM wagers WHERE bet_id=? AND user_id=?", (self.bet_id, interaction.user.id))
        existing = cur.fetchone()

        if existing:
            existing_choice, existing_amount = existing
            if existing_choice != self.choice:
                existing_label = self.option_a_label if existing_choice == "a" else self.option_b_label
                await interaction.response.send_message(
                    f"이미 **'{existing_label}'**에 배팅하셨습니다. 반대쪽에는 배팅할 수 없습니다. (같은 쪽에는 추가 배팅 가능)",
                    ephemeral=True
                )
                return
            add_points(interaction.user.id, -amount)
            cur.execute("UPDATE wagers SET amount = amount + ? WHERE bet_id=? AND user_id=?", (amount, self.bet_id, interaction.user.id))
            conn.commit()
            new_total = existing_amount + amount
            await interaction.response.send_message(f"만두 {amount}개를 추가로 배팅했습니다! (이 승부예측 총 배팅: {new_total}개)", ephemeral=True)
        else:
            add_points(interaction.user.id, -amount)
            cur.execute("INSERT INTO wagers (bet_id, user_id, choice, amount) VALUES (?, ?, ?, ?)",
                        (self.bet_id, interaction.user.id, self.choice, amount))
            conn.commit()
            await interaction.response.send_message(f"만두 {amount}개를 배팅했습니다!", ephemeral=True)

        await refresh_bet_message(self.bet_id, view=BetView(self.bet_id, self.option_a_label, self.option_b_label))

class BetView(discord.ui.View):
    def __init__(self, bet_id, option_a, option_b):
        super().__init__(timeout=None)
        self.bet_id = bet_id

        btn_a = discord.ui.Button(label=option_a, style=discord.ButtonStyle.green, custom_id=f"pred_a_{bet_id}")
        btn_b = discord.ui.Button(label=option_b, style=discord.ButtonStyle.red, custom_id=f"pred_b_{bet_id}")

        async def on_a(interaction: discord.Interaction):
            current_points = get_points(interaction.user.id)
            cur.execute("SELECT choice FROM wagers WHERE bet_id=? AND user_id=?", (bet_id, interaction.user.id))
            existing = cur.fetchone()
            extra_mode = bool(existing and existing[0] == "a")
            if existing and existing[0] == "b":
                await interaction.response.send_message(f"이미 **'{option_b}'**에 배팅하셨습니다. 반대쪽에는 배팅할 수 없습니다.", ephemeral=True)
                return
            modal = BetModal(bet_id, "a", option_a, option_b, current_points, extra_mode=extra_mode)
            await interaction.response.send_modal(modal)

        async def on_b(interaction: discord.Interaction):
            current_points = get_points(interaction.user.id)
            cur.execute("SELECT choice FROM wagers WHERE bet_id=? AND user_id=?", (bet_id, interaction.user.id))
            existing = cur.fetchone()
            extra_mode = bool(existing and existing[0] == "b")
            if existing and existing[0] == "a":
                await interaction.response.send_message(f"이미 **'{option_a}'**에 배팅하셨습니다. 반대쪽에는 배팅할 수 없습니다.", ephemeral=True)
                return
            modal = BetModal(bet_id, "b", option_a, option_b, current_points, extra_mode=extra_mode)
            await interaction.response.send_modal(modal)

        btn_a.callback = on_a
        btn_b.callback = on_b
        self.add_item(btn_a)
        self.add_item(btn_b)

async def close_betting_after_delay(bet_id, duration_seconds):
    await discord.utils.sleep_until(discord.utils.utcnow() + datetime.timedelta(seconds=duration_seconds))
    cur.execute("SELECT status FROM bets WHERE bet_id=?", (bet_id,))
    row = cur.fetchone()
    if row and row[0] == "open":
        await refresh_bet_message(bet_id, view=None)

class DurationSelectView(discord.ui.View):
    def __init__(self, creator_id, channel_id, title, option_a, option_b):
        super().__init__(timeout=60)
        self.creator_id = creator_id
        self.channel_id = channel_id
        self.title_text = title
        self.option_a = option_a
        self.option_b = option_b

        for label, seconds in DURATION_OPTIONS:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.blurple)

            async def callback(interaction: discord.Interaction, seconds=seconds, label=label):
                if interaction.user.id != self.creator_id:
                    await interaction.response.send_message("이 승부예측을 만든 본인만 제한시간을 선택할 수 있습니다.", ephemeral=True)
                    return

                cur.execute("SELECT bet_id FROM bets WHERE status='open'")
                if cur.fetchone():
                    await interaction.response.send_message("이미 다른 승부예측이 먼저 시작되었습니다. 이 예측은 생성되지 않습니다.", ephemeral=True)
                    self.stop()
                    return

                created_at = int(time.time())
                cur.execute(
                    "INSERT INTO bets (title, option_a, option_b, status, channel_id, created_at, creator_id, duration_seconds) VALUES (?, ?, ?, 'open', ?, ?, ?, ?)",
                    (self.title_text, self.option_a, self.option_b, self.channel_id, created_at, self.creator_id, seconds)
                )
                conn.commit()
                bet_id = cur.lastrowid

                stats = {"a": {"count": 0, "total": 0}, "b": {"count": 0, "total": 0}}
                embed = build_bet_embed(self.title_text, self.option_a, self.option_b, stats, "open", created_at, seconds)
                bet_view = BetView(bet_id, self.option_a, self.option_b)

                await interaction.response.edit_message(
                    content=f"✅ 제한시간 **{label}**으로 승부예측이 생성되었습니다!",
                    embed=None, view=None
                )
                channel = bot.get_channel(self.channel_id)
                msg = await channel.send(embed=embed, view=bet_view)
                cur.execute("UPDATE bets SET message_id=? WHERE bet_id=?", (msg.id, bet_id))
                conn.commit()

                bot.loop.create_task(close_betting_after_delay(bet_id, seconds))
                self.stop()

            btn.callback = callback
            self.add_item(btn)

@bot.group(invoke_without_command=True)
async def 승부예측(ctx):
    await ctx.send(
        '사용법:\n'
        '`!승부예측 생성 "제목" "성공옵션" "실패옵션"` (제한시간은 이후 버튼으로 선택)\n'
        '`!승부예측 종료 결과` (생성한 본인만 가능)\n'
        '`!승부예측 전체종료` (관리자 전용, 누가 만들었든 취소 가능)\n'
        '`!승부예측 상태` (디버깅용: 현재 상태 확인)'
    )

@승부예측.command(name="상태")
async def 승부예측_상태(ctx):
    cur.execute("SELECT bet_id, title, option_a, option_b, status, created_at, creator_id, duration_seconds FROM bets ORDER BY bet_id DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        await ctx.send("아직 생성된 승부예측 기록이 전혀 없습니다.")
        return
    bet_id, title, option_a, option_b, status, created_at, creator_id, duration_seconds = row
    creator = ctx.guild.get_member(creator_id)
    creator_name = creator.display_name if creator else str(creator_id)
    await ctx.send(
        f"가장 최근 승부예측 정보:\n"
        f"- ID: {bet_id}\n"
        f"- 제목: {title}\n"
        f"- 옵션: {option_a} / {option_b}\n"
        f"- 상태: {status}\n"
        f"- 생성자: {creator_name}\n"
        f"- 제한시간: {duration_seconds}초\n"
        f"- 생성 시각(유닉스): {created_at}"
    )

@승부예측.command(name="생성")
async def 승부예측_생성(ctx, 제목: str, 성공옵션: str, 실패옵션: str):
    try:
        cur.execute("SELECT bet_id, title, status FROM bets WHERE status='open'")
        existing = cur.fetchone()
        if existing:
            await ctx.send(f"이미 진행 중인 승부예측이 있습니다 (ID: {existing[0]}, 제목: {existing[1]}). 먼저 종료해주세요.")
            return

        view = DurationSelectView(ctx.author.id, ctx.channel.id, 제목, 성공옵션, 실패옵션)
        await ctx.send(
            f"🔮 **{제목}** ('{성공옵션}' vs '{실패옵션}')\n배팅 제한시간을 선택해주세요 (본인만 선택 가능):",
            view=view
        )
    except Exception as e:
        error_text = traceback.format_exc()
        print(f"[승부예측 생성 오류]\n{error_text}")
        await ctx.send(f"⚠️ 승부예측 생성 중 오류가 발생했습니다:\n```{str(e)}```")

@승부예측_생성.error
async def 승부예측_생성_error(ctx, error):
    if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
        await ctx.send('사용법: `!승부예측 생성 "제목" "성공옵션" "실패옵션"` (띄어쓰기가 있으면 큰따옴표로 감싸주세요)\n예: `!승부예측 생성 테스트 성공 실패`')
    else:
        error_text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        print(f"[승부예측 생성 명령어 오류]\n{error_text}")
        await ctx.send(f"⚠️ 오류가 발생했습니다:\n```{str(error)}```")

@승부예측.command(name="종료")
async def 승부예측_종료(ctx, *, 결과: str):
    cur.execute("SELECT bet_id, title, option_a, option_b, creator_id FROM bets WHERE status='open'")
    row = cur.fetchone()
    if not row:
        await ctx.send("진행 중인 승부예측이 없습니다.")
        return
    bet_id, title, option_a, option_b, creator_id = row

    if ctx.author.id != creator_id:
        creator = ctx.guild.get_member(creator_id)
        creator_name = creator.display_name if creator else "알 수 없음"
        await ctx.send(f"이 승부예측은 **{creator_name}**님이 생성한 것이라 본인만 종료할 수 있습니다. (관리자는 `!승부예측 전체종료`로 취소 가능)")
        return

    if 결과 == option_a:
        win_choice, lose_choice, win_status = "a", "b", "a"
    elif 결과 == option_b:
        win_choice, lose_choice, win_status = "b", "a", "b"
    else:
        await ctx.send(f"'{option_a}' 또는 '{option_b}' 중에서 정확히 입력해주세요.")
        return

    cur.execute("SELECT user_id, amount FROM wagers WHERE bet_id=? AND choice=?", (bet_id, win_choice))
    winners = cur.fetchall()
    cur.execute("SELECT user_id, amount FROM wagers WHERE bet_id=? AND choice=?", (bet_id, lose_choice))
    losers = cur.fetchall()

    total_win_pool = sum(a for _, a in winners)
    total_lose_pool = sum(a for _, a in losers)

    result_lines = []
    if total_win_pool == 0:
        for uid, amt in losers:
            add_points(uid, amt)
        result_lines.append("승리 측 참여자가 없어 배팅 만두가 전액 반환되었습니다.")
    else:
        for uid, amt in winners:
            share = int(total_lose_pool * (amt / total_win_pool))
            payout = amt + share
            add_points(uid, payout)
            member = ctx.guild.get_member(uid)
            name = member.display_name if member else str(uid)
            result_lines.append(f"{name}: +{payout}개 (원금 {amt} + 배당 {share})")

    cur.execute("UPDATE bets SET status=? WHERE bet_id=?", (win_status, bet_id))
    conn.commit()
    await refresh_bet_message(bet_id, view=None)

    result_embed = discord.Embed(title=f"🎉 승부예측 종료: {title}", description=f"결과: **{결과}**", color=discord.Color.green())
    result_embed.add_field(name="배당 내역", value="\n".join(result_lines) if result_lines else "없음", inline=False)
    await ctx.send(embed=result_embed)

@승부예측.command(name="전체종료")
@commands.has_permissions(administrator=True)
async def 승부예측_전체종료(ctx):
    cur.execute("SELECT bet_id, title FROM bets WHERE status='open'")
    row = cur.fetchone()
    if not row:
        await ctx.send("진행 중인 승부예측이 없습니다.")
        return
    bet_id, title = row

    cur.execute("SELECT user_id, amount FROM wagers WHERE bet_id=?", (bet_id,))
    all_wagers = cur.fetchall()
    for uid, amt in all_wagers:
        add_points(uid, amt)

    cur.execute("UPDATE bets SET status='cancelled' WHERE bet_id=?", (bet_id,))
    conn.commit()
    await refresh_bet_message(bet_id, view=None)

    await ctx.send(f"승부예측 '{title}'이(가) 취소되었습니다. 배팅했던 만두는 전액 환불되었습니다.")

@승부예측_전체종료.error
async def 승부예측_전체종료_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("이 명령어는 관리자만 사용할 수 있습니다.")

# ---------- 봇 시작 ----------
@bot.event
async def on_ready():
    if not voice_point_task.is_running():
        voice_point_task.start()

    cur.execute("SELECT bet_id, option_a, option_b, status, created_at, duration_seconds FROM bets WHERE status='open'")
    row = cur.fetchone()
    if row:
        bet_id, option_a, option_b, status, created_at, duration_seconds = row
        remaining = duration_seconds - (int(time.time()) - created_at)
        if remaining > 0:
            bot.add_view(BetView(bet_id, option_a, option_b))
            bot.loop.create_task(close_betting_after_delay(bet_id, duration_seconds))

    restore_pending_temp_changes()

    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(f"슬래시 명령어 동기화 실패: {e}")

    print(f"{bot.user} 봇이 온라인 상태입니다! (버전 v{BOT_VERSION})")

client = bot
client.run(TOKEN)
