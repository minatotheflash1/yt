import discord
from discord.ext import tasks
import feedparser
import asyncpg
import aiohttp
import os
from dotenv import load_dotenv

# Local env file load (Railway te eta auto ignore hobe jodi file na thake)
load_dotenv()

# --- Configuration from Variables ---
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DISCORD_CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 0))
YOUTUBE_CHANNEL_ID = os.getenv('YOUTUBE_CHANNEL_ID')
PING_ROLE_ID = os.getenv('PING_ROLE_ID') # Optional
DATABASE_URL = os.getenv('DATABASE_URL')

YOUTUBE_RSS = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
YOUTUBE_CHANNEL_LINK = f"https://www.youtube.com/channel/{YOUTUBE_CHANNEL_ID}"

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
db_pool = None

async def init_db():
    global db_pool
    # Railway direct connection string (DATABASE_URL) provide kore
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS posted_videos (
                video_id VARCHAR(255) PRIMARY KEY,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

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
    if not DISCORD_CHANNEL_ID:
        return

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
            
            # Embed Design
            embed = discord.Embed(
                title=f"🔴 {video_title}",
                url=video_link,
                description=ai_description,
                color=discord.Color.brand_red()
            )
            embed.set_author(name="Aura MINATO", url=YOUTUBE_CHANNEL_LINK)
            embed.set_image(url=thumbnail_url)
            embed.set_footer(text="Official Stream Alert")
            
            # Smart Ping System
            ping_text = f"<@&{PING_ROLE_ID}>" if PING_ROLE_ID else "@everyone"
            
            channel = bot.get_channel(DISCORD_CHANNEL_ID)
            await channel.send(content=f"{ping_text} **New Content is Live!**", embed=embed)
            
            await conn.execute('INSERT INTO posted_videos (video_id) VALUES ($1)', video_id)

@bot.event
async def on_ready():
    await init_db()
    # Custom Bot Presence added
    activity = discord.Game(name="Free Fire")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    
    print(f'System Online: Logged in as {bot.user}')
    check_youtube.start()

bot.run(DISCORD_TOKEN)
