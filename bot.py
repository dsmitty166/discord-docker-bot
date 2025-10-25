import discord
from discord import app_commands
import subprocess
import os
import logging
import requests
import socket
from datetime import datetime, timezone

# --- CONFIG ---
TOKEN = os.getenv("DISCORD_TOKEN")
ALLOWED_ROLE = int(os.getenv("ALLOWED_ROLE", "0"))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
HOSTNAME = socket.gethostname()

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docker_discord_bot")

# --- DISCORD CLIENT ---
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# --- UTILITY FUNCTIONS ---
def run_docker_cmd(cmd):
    """Run docker CLI commands and return stdout."""
    result = subprocess.run(["docker"] + cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()

def get_containers(only_running=False, only_stopped=False):
    """Return a list of (name, status) tuples."""
    cmd = ["ps", "-a", "--format", "{{.Names}}|{{.Status}}"]
    if only_running:
        cmd = ["ps", "--format", "{{.Names}}|{{.Status}}"]
    try:
        output = run_docker_cmd(cmd)
        containers = []
        for line in output.splitlines():
            if "|" in line:
                name, status = line.split("|", 1)
                name, status = name.strip(), status.strip()
                if only_stopped and status.lower().startswith("up"):
                    continue
                containers.append((name, status))
        logger.info(f"Found containers: {containers}")
        return containers
    except Exception as e:
        logger.error(f"Error fetching containers: {e}")
        return []

def send_webhook(user, container_name, action):
    """Send a Discord webhook notification for container actions."""
    if not WEBHOOK_URL:
        logger.warning("Webhook URL not set, skipping notification.")
        return

    colors = {
        "start": 0x00FF00,   # 🟢 green
        "restart": 0xFFFF00, # 🟡 yellow
        "stop": 0xFF0000     # 🔴 red
    }
    color = colors.get(action, 0x808080)

    embed = {
        "title": f"Container {action.capitalize()} Executed",
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [
            {"name": "Container", "value": f"`{container_name}`", "inline": True},
            {"name": "User", "value": f"{user.name} ({user.id})", "inline": True},
            {"name": "Server Host", "value": f"`{HOSTNAME}`", "inline": False},
        ],
        "footer": {"text": "Docker Discord Bot"},
    }

    try:
        requests.post(WEBHOOK_URL, json={"embeds": [embed]})
        logger.info(f"Sent webhook: {action} on {container_name} by {user}")
    except Exception as e:
        logger.error(f"Failed to send webhook: {e}")

def is_authorized(interaction: discord.Interaction):
    """Check if user has allowed role or is admin."""
    if interaction.user.guild_permissions.administrator:
        return True
    return any(role.id == ALLOWED_ROLE for role in interaction.user.roles)

# --- DISCORD EVENTS ---
@bot.event
async def on_ready():
    await tree.sync()
    logger.info(f"✅ Logged in as {bot.user} — slash commands synced")

# --- SLASH COMMANDS ---
@tree.command(name="containers", description="List all Docker containers")
async def containers(interaction: discord.Interaction):
    if not is_authorized(interaction):
        await interaction.response.send_message("❌ You don't have permission to do that.", ephemeral=True)
        return

    try:
        containers = get_containers()
        if not containers:
            await interaction.response.send_message("No containers found.", ephemeral=True)
            return
        msg = "\n".join(f"**{name}** — {status}" for name, status in containers)
        await interaction.response.send_message(f"📦 **Containers:**\n{msg}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error: {e}", ephemeral=True)

@tree.command(name="restart", description="Restart a Docker container by name")
@app_commands.describe(container="The name of the Docker container to restart")
async def restart(interaction: discord.Interaction, container: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("❌ You don't have permission to do that.", ephemeral=True)
        return

    try:
        run_docker_cmd(["restart", container])
        send_webhook(interaction.user, container, "restart")
        await interaction.response.send_message(f"🟡 Restarted `{container}` successfully.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error restarting `{container}`: {e}", ephemeral=True)

@tree.command(name="stop", description="Stop a Docker container by name")
@app_commands.describe(container="The name of the Docker container to stop")
async def stop(interaction: discord.Interaction, container: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("❌ You don't have permission to do that.", ephemeral=True)
        return

    try:
        run_docker_cmd(["stop", container])
        send_webhook(interaction.user, container, "stop")
        await interaction.response.send_message(f"🔴 Stopped `{container}` successfully.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error stopping `{container}`: {e}", ephemeral=True)

@tree.command(name="start", description="Start a Docker container by name")
@app_commands.describe(container="The name of the Docker container to start")
async def start(interaction: discord.Interaction, container: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("❌ You don't have permission to do that.", ephemeral=True)
        return

    try:
        run_docker_cmd(["start", container])
        send_webhook(interaction.user, container, "start")
        await interaction.response.send_message(f"🟢 Started `{container}` successfully.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error starting `{container}`: {e}", ephemeral=True)

# --- AUTOCOMPLETE ---
@restart.autocomplete("container")
@stop.autocomplete("container")
async def running_container_autocomplete(_: discord.Interaction, current: str):
    containers = get_containers(only_running=True)
    choices = []
    for name, status in containers:
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=f"{name} ({status})", value=name))
    return choices[:25]

@start.autocomplete("container")
async def stopped_container_autocomplete(_: discord.Interaction, current: str):
    containers = get_containers(only_stopped=True)
    choices = []
    for name, status in containers:
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=f"{name} ({status})", value=name))
    return choices[:25]

# --- RUN BOT ---
bot.run(TOKEN)
