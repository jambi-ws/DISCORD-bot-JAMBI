import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import os
import sqlite3
import time

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
    bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    option_a TEXT,
    option_b TEXT,
    status TEXT DEFAULT 'open',
    channel_id INTEGER,
    message_id INTEGER,
    created_at INTEGER)""")
cur.execute("""CREATE TABLE IF NOT EXISTS wagers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_id INTEGER,
    user_id INTEGER,
    choice TEXT,
    amount INTEGER)""")
conn.commit()

BETTING_DURATION = 180  # 3분

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

# ---------- 만두 확인 / 전체 / 지급 ----------
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

@bot.tree.command(name="만두지급", description="[관리자] 특정 사용자에게 만두를 지급합니다")
@app_commands.describe(대상="만두를 지급할 사용자", 개수="지급할 만두 개수")
async def 만두지급(interaction: discord.Interaction, 대상: discord.Member, 개수: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("이 명령어는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    add_points(대상.id, 개수)
    new_total = get_points(대상.id)
    await interaction.response.send_message(
        f"{대상.mention}님에게 만두 **{개수}개**를 지급했습니다. (현재 보유: {new_total}개) 🥟"
    )

# ---------- 승부예측 시스템 ----------
async def get_bet_stats(bet_id):
    cur.execute("SELECT choice, COUNT(*), SUM(amount) FROM wagers WHERE bet_id=? GROUP BY choice", (bet_id,))
    stats = {"a": {"count": 0, "total": 0}, "b": {"count": 0, "total": 0}}
    for choice, count, total in cur.fetchall():
        stats[choice] = {"count": count, "total": total or 0}
    return stats

def build_bet_embed(title, option_a, option_b, stats, status, created_at):
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
        remaining = BETTING_DURATION - (int(time.time()) - created_at)
        if remaining > 0:
            embed.set_footer(text=f"상태: 진행중 (배팅 마감까지 약 {remaining // 60}분 {remaining % 60}초)")
        else:
            embed.set_footer(text="상태: 배팅 마감됨 · 결과 발표 대기중")
    elif status == "cancelled":
        embed.set_footer(text="상태: 취소됨 (전액 환불)")
    else:
        winner_name = option_a if status == "a" else option_b
        embed.set_footer(text=f"상태: 종료 (승리: {winner_name})")
    return embed

async def refresh_bet_message(bet_id, view=None):
    cur.execute("SELECT title, option_a, option_b, status, channel_id, message_id, created_at FROM bets WHERE bet_id=?", (bet_id,))
    row = cur.fetchone()
    if not row:
        return
    title, option_a, option_b, status, channel_id, message_id, created_at = row
    stats = await get_bet_stats(bet_id)
    embed = build_bet_embed(title, option_a, option_b, stats, status, created_at)

    channel = bot.get_channel(channel_id)
    if channel and message_id:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(embed=embed, view=view)
        except discord.NotFound:
            pass

class BetModal(discord.ui.Modal):
    def __init__(self, bet_id, choice, choice_label):
        super().__init__(title=f"'{choice_label}'에 배팅하기")
        self.bet_id = bet_id
        self.choice = choice
        self.amount_input = discord.ui.TextInput(label="배팅할 만두 수", placeholder="예: 50")
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        cur.execute("SELECT status, created_at FROM bets WHERE bet_id=?", (self.bet_id,))
        row = cur.fetchone()
        if not row or row[0] != "open":
            await interaction.response.send_message("이 승부예측은 더 이상 참여할 수 없습니다.", ephemeral=True)
            return
        if int(time.time()) - row[1] > BETTING_DURATION:
            await interaction.response.send_message("배팅 가능 시간(3분)이 지났습니다.", ephemeral=True)
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

        cur.execute("SELECT amount FROM wagers WHERE bet_id=? AND user_id=?", (self.bet_id, interaction.user.id))
        if cur.fetchone():
            await interaction.response.send_message("이미 이 승부예측에 참여하셨습니다.", ephemeral=True)
            return

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
            modal = BetModal(bet_id, "a", option_a)
            modal.option_a_label = option_a
            modal.option_b_label = option_b
            await interaction.response.send_modal(modal)

        async def on_b(interaction: discord.Interaction):
            modal = BetModal(bet_id, "b", option_b)
            modal.option_a_label = option_a
            modal.option_b_label = option_b
            await interaction.response.send_modal(modal)

        btn_a.callback = on_a
        btn_b.callback = on_b
        self.add_item(btn_a)
        self.add_item(btn_b)

async def close_betting_after_delay(bet_id):
    await discord.utils.sleep_until(discord.utils.utcnow() + __import__("datetime").timedelta(seconds=BETTING_DURATION))
    cur.execute("SELECT status FROM bets WHERE bet_id=?", (bet_id,))
    row = cur.fetchone()
    if row and row[0] == "open":
        await refresh_bet_message(bet_id, view=None)  # 베팅 마감: 버튼 제거

@bot.tree.command(name="승부예측생성", description="새로운 승부예측을 생성합니다 (베팅 3분 제한)")
@app_commands.describe(제목="승부예측 제목", 성공옵션="성공 쪽 이름", 실패옵션="실패 쪽 이름")
async def 승부예측생성(interaction: discord.Interaction, 제목: str, 성공옵션: str, 실패옵션: str):
    cur.execute("SELECT bet_id FROM bets WHERE status='open'")
    if cur.fetchone():
        await interaction.response.send_message("이미 진행 중인 승부예측이 있습니다. 먼저 종료해주세요.", ephemeral=True)
        return

    created_at = int(time.time())
    cur.execute(
        "INSERT INTO bets (title, option_a, option_b, status, channel_id, created_at) VALUES (?, ?, ?, 'open', ?, ?)",
        (제목, 성공옵션, 실패옵션, interaction.channel_id, created_at)
    )
    conn.commit()
    bet_id = cur.lastrowid

    stats = {"a": {"count": 0, "total": 0}, "b": {"count": 0, "total": 0}}
    embed = build_bet_embed(제목, 성공옵션, 실패옵션, stats, "open", created_at)
    view = BetView(bet_id, 성공옵션, 실패옵션)

    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    cur.execute("UPDATE bets SET message_id=? WHERE bet_id=?", (msg.id, bet_id))
    conn.commit()

    bot.loop.create_task(close_betting_after_delay(bet_id))

@app_commands.command(name="_dummy")
async def _dummy(interaction: discord.Interaction):
    pass

async def result_autocomplete(interaction: discord.Interaction, current: str):
    cur.execute("SELECT option_a, option_b FROM bets WHERE status='open'")
    row = cur.fetchone()
    if not row:
        return []
    option_a, option_b = row
    choices = [option_a, option_b]
    return [
        app_commands.Choice(name=c, value=c)
        for c in choices if current.lower() in c.lower()
    ][:25]

@bot.tree.command(name="승부예측종료", description="진행 중인 승부예측의 결과를 발표하고 마감합니다")
@app_commands.describe(결과="승리한 쪽의 이름을 선택하세요")
@app_commands.autocomplete(결과=result_autocomplete)
async def 승부예측종료(interaction: discord.Interaction, 결과: str):
    cur.execute("SELECT bet_id, title, option_a, option_b FROM bets WHERE status='open'")
    row = cur.fetchone()
    if not row:
        await interaction.response.send_message("진행 중인 승부예측이 없습니다.", ephemeral=True)
        return
    bet_id, title, option_a, option_b = row

    if 결과 == option_a:
        win_choice, lose_choice, win_status = "a", "b", "a"
    elif 결과 == option_b:
        win_choice, lose_choice, win_status = "b", "a", "b"
    else:
        await interaction.response.send_message(f"'{option_a}' 또는 '{option_b}' 중에서 선택해주세요.", ephemeral=True)
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
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else str(uid)
            result_lines.append(f"{name}: +{payout}개 (원금 {amt} + 배당 {share})")

    cur.execute("UPDATE bets SET status=? WHERE bet_id=?", (win_status, bet_id))
    conn.commit()
    await refresh_bet_message(bet_id, view=None)

    result_embed = discord.Embed(title=f"🎉 승부예측 종료: {title}", description=f"결과: **{결과}**", color=discord.Color.green())
    result_embed.add_field(name="배당 내역", value="\n".join(result_lines) if result_lines else "없음", inline=False)
    await interaction.response.send_message(embed=result_embed)

@bot.tree.command(name="승부예측전체종료", description="[관리자] 진행 중인 승부예측을 취소하고 전액 환불합니다")
async def 승부예측전체종료(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("이 명령어는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    cur.execute("SELECT bet_id, title FROM bets WHERE status='open'")
    row = cur.fetchone()
    if not row:
        await interaction.response.send_message("진행 중인 승부예측이 없습니다.", ephemeral=True)
        return
    bet_id, title = row

    cur.execute("SELECT user_id, amount FROM wagers WHERE bet_id=?", (bet_id,))
    all_wagers = cur.fetchall()
    for uid, amt in all_wagers:
        add_points(uid, amt)

    cur.execute("UPDATE bets SET status='cancelled' WHERE bet_id=?", (bet_id,))
    conn.commit()
    await refresh_bet_message(bet_id, view=None)

    await interaction.response.send_message(f"승부예측 '{title}'이(가) 취소되었습니다. 배팅했던 만두는 전액 환불되었습니다.")

# ---------- 봇 시작 ----------
@bot.event
async def on_ready():
    if not voice_point_task.is_running():
        voice_point_task.start()

    cur.execute("SELECT bet_id, option_a, option_b, status, created_at FROM bets WHERE status='open'")
    row = cur.fetchone()
    if row:
        bet_id, option_a, option_b, status, created_at = row
        remaining = BETTING_DURATION - (int(time.time()) - created_at)
        if remaining > 0:
            bot.add_view(BetView(bet_id, option_a, option_b))
            bot.loop.create_task(close_betting_after_delay(bet_id))

    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(f"슬래시 명령어 동기화 실패: {e}")

    print(f"{bot.user} 봇이 온라인 상태입니다!")

client = bot
client.run(TOKEN)
