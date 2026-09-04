import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os
import sqlite3

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
cur.execute("""CREATE TABLE IF NOT EXISTS bets (
    bet_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
    status TEXT DEFAULT 'open', channel_id INTEGER, message_id INTEGER)""")
cur.execute("""CREATE TABLE IF NOT EXISTS wagers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bet_id INTEGER,
    user_id INTEGER, choice TEXT, amount INTEGER)""")
conn.commit()

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

# ---------- 만두 확인 / 전체 (슬래시 명령어 - 나만 보기) ----------
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

# ---------- 만두 지급 (관리자 전용, 텍스트 명령어 유지) ----------
@bot.group(invoke_without_command=True)
async def 만두(ctx):
    await ctx.send("사용법: `/만두확인` / `/만두전체` / `!만두 지급 @사용자 개수` (관리자 전용)")

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

# ---------- 배팅 시스템 ----------
async def get_bet_totals(bet_id):
    cur.execute("SELECT choice, SUM(amount) FROM wagers WHERE bet_id=? GROUP BY choice", (bet_id,))
    totals = {"성공": 0, "실패": 0}
    for choice, total in cur.fetchall():
        totals[choice] = total or 0
    return totals

async def update_bet_embed(bet_id):
    cur.execute("SELECT title, channel_id, message_id, status FROM bets WHERE bet_id=?", (bet_id,))
    row = cur.fetchone()
    if not row:
        return
    title, channel_id, message_id, status = row
    totals = await get_bet_totals(bet_id)

    embed = discord.Embed(title=f"🎲 배팅: {title}", color=discord.Color.blurple())
    embed.add_field(name="✅ 성공", value=f"{totals['성공']}개", inline=True)
    embed.add_field(name="❌ 실패", value=f"{totals['실패']}개", inline=True)
    embed.set_footer(text="상태: 진행중" if status == "open" else f"상태: 종료 ({status} 승리)")

    channel = bot.get_channel(channel_id)
    if channel and message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed, view=BetView(bet_id) if status == "open" else None)
        except discord.NotFound:
            pass

class BetModal(discord.ui.Modal):
    def __init__(self, bet_id, choice):
        super().__init__(title=f"{choice}에 배팅하기")
        self.bet_id = bet_id
        self.choice = choice
        self.amount_input = discord.ui.TextInput(label="배팅할 만두 수", placeholder="예: 50")
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
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

        cur.execute("SELECT amount FROM wagers WHERE bet_id=? AND user_id=?", (self.bet_id, interaction.user.id))
        if cur.fetchone():
            await interaction.response.send_message("이미 이 배팅에 참여하셨습니다.", ephemeral=True)
            return

        add_points(interaction.user.id, -amount)
        cur.execute("INSERT INTO wagers (bet_id, user_id, choice, amount) VALUES (?, ?, ?, ?)",
                    (self.bet_id, interaction.user.id, self.choice, amount))
        conn.commit()

        await interaction.response.send_message(f"'{self.choice}'에 만두 {amount}개를 배팅했습니다!", ephemeral=True)
        await update_bet_embed(self.bet_id)

class BetView(discord.ui.View):
    def __init__(self, bet_id):
        super().__init__(timeout=None)
        self.bet_id = bet_id

    @discord.ui.button(label="✅ 성공", style=discord.ButtonStyle.green, custom_id="bet_success")
    async def success_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetModal(self.bet_id, "성공"))

    @discord.ui.button(label="❌ 실패", style=discord.ButtonStyle.red, custom_id="bet_fail")
    async def fail_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetModal(self.bet_id, "실패"))

@bot.command(name="배팅생성")
@commands.has_permissions(administrator=True)
async def 배팅생성(ctx, *, title: str):
    cur.execute("SELECT bet_id FROM bets WHERE status='open'")
    if cur.fetchone():
        await ctx.send("이미 진행 중인 배팅이 있습니다. 먼저 `!배팅종료`로 마무리해주세요.")
        return

    cur.execute("INSERT INTO bets (title, status, channel_id) VALUES (?, 'open', ?)", (title, ctx.channel.id))
    conn.commit()
    bet_id = cur.lastrowid

    embed = discord.Embed(title=f"🎲 배팅: {title}", color=discord.Color.blurple())
    embed.add_field(name="✅ 성공", value="0개", inline=True)
    embed.add_field(name="❌ 실패", value="0개", inline=True)
    embed.set_footer(text="상태: 진행중")

    msg = await ctx.send(embed=embed, view=BetView(bet_id))
    cur.execute("UPDATE bets SET message_id=? WHERE bet_id=?", (msg.id, bet_id))
    conn.commit()

@bot.command(name="배팅종료")
@commands.has_permissions(administrator=True)
async def 배팅종료(ctx, 결과: str):
    if 결과 not in ("성공", "실패"):
        await ctx.send("결과는 `성공` 또는 `실패`로 입력해주세요. 예: `!배팅종료 성공`")
        return

    cur.execute("SELECT bet_id, title FROM bets WHERE status='open'")
    row = cur.fetchone()
    if not row:
        await ctx.send("진행 중인 배팅이 없습니다.")
        return
    bet_id, title = row

    cur.execute("SELECT user_id, amount FROM wagers WHERE bet_id=? AND choice=?", (bet_id, 결과))
    winners = cur.fetchall()
    반대 = "실패" if 결과 == "성공" else "성공"
    cur.execute("SELECT user_id, amount FROM wagers WHERE bet_id=? AND choice=?", (bet_id, 반대))
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

    cur.execute("UPDATE bets SET status=? WHERE bet_id=?", (결과, bet_id))
    conn.commit()
    await update_bet_embed(bet_id)

    embed = discord.Embed(title=f"🎉 배팅 종료: {title}", description=f"결과: **{결과}**", color=discord.Color.green())
    embed.add_field(name="배당 내역", value="\n".join(result_lines) if result_lines else "없음", inline=False)
    await ctx.send(embed=embed)

# ---------- 봇 시작 ----------
@bot.event
async def on_ready():
    if not voice_point_task.is_running():
        voice_point_task.start()

    cur.execute("SELECT bet_id FROM bets WHERE status='open'")
    row = cur.fetchone()
    if row:
        bot.add_view(BetView(row[0]))

    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(f"슬래시 명령어 동기화 실패: {e}")

    print(f"{bot.user} 봇이 온라인 상태입니다!")

client = bot
client.run(TOKEN)
