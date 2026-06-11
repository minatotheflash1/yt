import discord
from discord.ext import commands, tasks
import feedparser
import asyncpg
import aiohttp
import os
import asyncio
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DISCORD_CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 0))
YOUTUBE_CHANNEL_ID = os.getenv('YOUTUBE_CHANNEL_ID')
PING_ROLE_ID = os.getenv('PING_ROLE_ID') 
DATABASE_URL = os.getenv('DATABASE_URL')

YOUTUBE_RSS = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
YOUTUBE_CHANNEL_LINK = f"https://www.youtube.com/channel/{YOUTUBE_CHANNEL_ID}"

intents = discord.Intents.default()
intents.message_content = True  
intents.voice_states = True     
intents.members = True # এটি কাজ করার জন্য Developer Portal-এ Members Intent On থাকতে হবে

bot = commands.Bot(command_prefix="!", intents=intents)
db_pool = None

song_queues = {}
current_songs = {}

yt_dlp.utils.bug_reports_message = lambda: ''
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0] 
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

def play_next(channel, guild_id):
    vc = channel.guild.voice_client
    if vc and guild_id in song_queues and len(song_queues[guild_id]) > 0:
        player = song_queues[guild_id].pop(0)
        current_songs[guild_id] = player
        vc.play(player, after=lambda e: play_next(channel, guild_id))
        asyncio.run_coroutine_threadsafe(channel.send(f"🎶 **Ekhon bajtesey:** **{player.title}**"), bot.loop)
    else:
        if guild_id in current_songs:
            del current_songs[guild_id]

async def init_db():
    global db_pool
    if DATABASE_URL:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL)
            async with db_pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS posted_videos (
                        video_id VARCHAR(255) PRIMARY KEY,
                        posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
        except Exception as e:
            print(f"Database Initialization Error: {e}")

async def generate_deepseek_description(title):
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You are a hype-man for a gamer/streamer. Write a very short, highly engaging 2-line description for their new YouTube video or live stream to post on Discord. Use relevant emojis."},
            {"role": "user", "content": f"The video title is: {title}"}
        ]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"DeepSeek API Error: {e}")
    return "🔥 Streaming NOW or New Video Dropped! Don't miss out, join the action!"

@tasks.loop(minutes=3)
async def check_youtube():
    if not DISCORD_CHANNEL_ID or not db_pool:
        return

    try:
        feed = feedparser.parse(YOUTUBE_RSS)
        if not feed.entries:
            return
            
        latest_video = feed.entries[0]
        video_id = latest_video.yt_videoid
        video_link = latest_video.link
        video_title = latest_video.title
        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

        async with db_pool.acquire() as conn:
            exists = await conn.fetchval('SELECT EXISTS(SELECT 1 FROM posted_videos WHERE video_id = $1)', video_id)
            
            if not exists:
                print(f"New content detected: {video_title}")
                ai_description = await generate_deepseek_description(video_title)
                
                embed = discord.Embed(
                    title=f"🔴 {video_title}",
                    url=video_link,
                    description=ai_description,
                    color=discord.Color.brand_red()
                )
                embed.set_author(name="Aura MINATO", url=YOUTUBE_CHANNEL_LINK)
                embed.set_image(url=thumbnail_url)
                embed.set_footer(text="Official Stream Alert")
                
                ping_text = f"<@&{PING_ROLE_ID}>" if PING_ROLE_ID else "@everyone"
                
                channel = bot.get_channel(DISCORD_CHANNEL_ID)
                if not channel:
                    channel = await bot.fetch_channel(DISCORD_CHANNEL_ID)
                
                if channel:
                    await channel.send(content=f"{ping_text} **New Content is Live!**", embed=embed)
                    await conn.execute('INSERT INTO posted_videos (video_id) VALUES ($1)', video_id)
                else:
                    print(f"Error: Discord Channel ID {DISCORD_CHANNEL_ID} not found!")
                    
    except Exception as e:
        print(f"YouTube Loop Error: {e}")

# --- FIX: Type Checking for Members ---

@bot.tree.command(name="testalert", description="Check if the bot can send YouTube alerts to the text channel")
async def testalert(interaction: discord.Interaction):
    if not DISCORD_CHANNEL_ID:
        await interaction.response.send_message("❌ `.env` file e `DISCORD_CHANNEL_ID` set kora nei!", ephemeral=True)
        return
        
    try:
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            channel = await bot.fetch_channel(DISCORD_CHANNEL_ID)
            
        await channel.send("✅ **TEST SUCCESSFUL:** Bot ei channel e YouTube alert pathate parbe!")
        await interaction.response.send_message(f"✅ Test message sent to <#{DISCORD_CHANNEL_ID}>", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed! Error: `{e}`", ephemeral=True)

@bot.tree.command(name="join", description="Voice channel e join korbe (Deafen mode e)")
async def join(interaction: discord.Interaction):
    # Server e command na dile atke dibe
    if not interaction.guild:
        await interaction.response.send_message("❌ Ei command ti shudhu server e kaj korbe!", ephemeral=True)
        return

    # Check kora hocche user asolei Member kina ebong VC te ache kina
    if isinstance(interaction.user, discord.Member) and interaction.user.voice:
        channel = interaction.user.voice.channel
        if not interaction.guild.voice_client:
            await channel.connect(self_deaf=True)
            await interaction.response.send_message(f"✅ **{channel.name}** te join korechi! (Deafen mode active 🎧)")
        else:
            await interaction.response.send_message("⚠️ Ami idomoddhei ekta voice channel e achi.")
    else:
        await interaction.response.send_message("❌ Apni kono voice channel e nei! Age ekta VC te join korun.", ephemeral=True)

@bot.tree.command(name="play", description="YouTube link ba gaaner nam diye play korun")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer() 

    if not interaction.guild:
        await interaction.followup.send("❌ Ei command ti shudhu server e kaj korbe!")
        return

    if not isinstance(interaction.user, discord.Member) or not interaction.user.voice:
        await interaction.followup.send("❌ Apni kono voice channel e nei! (VC te join kore abar try korun)")
        return

    vc = interaction.guild.voice_client
    if not vc:
        vc = await interaction.user.voice.channel.connect(self_deaf=True)

    guild_id = interaction.guild.id
    if guild_id not in song_queues:
        song_queues[guild_id] = []

    search_query = query if query.startswith(('http://', 'https://', 'www.')) else f"ytsearch:{query}"

    try:
        player = await YTDLSource.from_url(search_query, loop=bot.loop, stream=True)
        
        if vc.is_playing() or vc.is_paused():
            song_queues[guild_id].append(player)
            await interaction.followup.send(f"⏳ Queue te add kora holo: **{player.title}**")
        else:
            current_songs[guild_id] = player
            vc.play(player, after=lambda e: play_next(interaction.channel, guild_id))
            await interaction.followup.send(f"🎶 Ekhon bajthesey: **{player.title}**")
            
    except Exception as e:
        print(f"Play Error: {e}")
        await interaction.followup.send("❌ Gaan khuje pete ba play korte somossa hoyeche. Abar chesta korun.")

@bot.tree.command(name="pause", description="Cholotman gaan ti pause korun")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Gaan pause kora holo!")
    else:
        await interaction.response.send_message("⚠️ Kono gaan play hocche na.", ephemeral=True)

@bot.tree.command(name="resume", description="Pause kora gaan ti abar shuru korun")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Gaan abar resume kora holo!")
    else:
        await interaction.response.send_message("⚠️ Kono gaan pause kora nei.", ephemeral=True)

@bot.tree.command(name="skip", description="Cholotman gaan ti skip kore porer gaan e jaan")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("⏭️ Next gaan chalanor jonno skip kora holo!")
    else:
        await interaction.response.send_message("⚠️ Skip korar moto kono gaan bajche na.", ephemeral=True)

@bot.tree.command(name="queue", description="Queue te thaka porer gaan gulo dekhun")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id in song_queues and len(song_queues[guild_id]) > 0:
        queue_list = "\n".join([f"{i+1}. {song.title}" for i, song in enumerate(song_queues[guild_id][:10])])
        if len(song_queues[guild_id]) > 10:
            queue_list += f"\n...ebong aro {len(song_queues[guild_id]) - 10} ti gaan!"
        await interaction.response.send_message(f"📋 **Upcoming Songs Queue:**\n{queue_list}")
    else:
        await interaction.response.send_message("📋 Queue khali! Line-up e kono gaan nei.")

@bot.tree.command(name="nowplaying", description="Ekhon kon gaan ti bajche tar details dekhun")
async def nowplaying(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id in current_songs and interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        await interaction.response.send_message(f"🎵 **Ekhon bajthesey:** {current_songs[guild_id].title}")
    else:
        await interaction.response.send_message("⚠️ Ekhon kono gaan bajche na.")

@bot.tree.command(name="leave", description="Gaan bondho kore queue clear korun ebong VC theke ber hon")
async def leave(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    
    if guild_id in song_queues:
        song_queues[guild_id].clear()
    if guild_id in current_songs:
        del current_songs[guild_id]
        
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("👋 Voice channel theke ber hoye gelam ebong queue clear hoyeche!")
    else:
        await interaction.response.send_message("⚠️ Ami kono voice channel e nei!", ephemeral=True)

@bot.event
async def on_ready():
    await init_db()
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Sync Error: {e}")
        
    activity = discord.Game(name="Free Fire")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    
    print(f'System Online: Logged in as {bot.user}')
    check_youtube.start()

if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("Error: DISCORD_TOKEN missing!")
