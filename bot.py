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
HOSTNAME = os.getenv("HOSTNAME", socket.gethostname())
ENABLE_AAF = os.getenv("ENABLE_AAF_RENAME", "false").lower() == "true"

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

import fnmatch  # add this import near the top of bot.py

def get_containers(only_running=False, only_stopped=False):
    """Return a list of (name, status) tuples, filtered by CONTAINER_FILTER (supports wildcards)."""
    cmd = ["ps", "-a", "--format", "{{.Names}}|{{.Status}}"]
    if only_running:
        cmd = ["ps", "--format", "{{.Names}}|{{.Status}}"]

    container_filter = os.getenv("CONTAINER_FILTER", "").strip()
    try:
        output = run_docker_cmd(cmd)
        containers = []
        for line in output.splitlines():
            if "|" in line:
                name, status = line.split("|", 1)
                name, status = name.strip(), status.strip()
                if only_stopped and status.lower().startswith("up"):
                    continue

                # ✅ Apply filter with wildcard support (*fs25*, fs25*, *fs25)
                if container_filter and container_filter != "*":
                    if not fnmatch.fnmatch(name, container_filter):
                        continue

                containers.append((name, status))
        return containers
    except Exception as e:
        logger.error(f"Error fetching containers: {e}")
        return []

def call_external_script(container):
    """Optionally run AAF rename script before restart and capture output."""
    if os.getenv("ENABLE_AAF_RENAME", "false").lower() != "true":
        logger.info("AAF rename disabled in .env")
        return None, None

    try:
        result = subprocess.run(
            ["/app/scripts/pre_restart.sh", container],
            capture_output=True,   # 👈 REQUIRED to capture stdout
            text=True              # 👈 converts bytes to string
        )
        logger.info(result.stdout)  # log everything from the script

        nbspaces, gameline = None, None
        for line in result.stdout.splitlines():
            if line.startswith("WEBHOOK_NBSPS_WRITTEN:"):
                nbspaces = line.split(":", 1)[1].strip()
            if line.startswith("WEBHOOK_GAME_LINE:"):
                gameline = line.split(":", 1)[1].strip()

        return nbspaces, gameline
    except Exception as e:
        logger.warning(f"AAF rename script failed for {container}: {e}")
        return None, None

def send_webhook(user, container_name, action, nbspaces=None, gameline=None):
    """Send a Discord webhook notification for container actions."""
    if not WEBHOOK_URL:
        logger.warning("Webhook URL not set, skipping notification.")
        return

    # Base colors
    colors = {
        "start": 0x00FF00,   # 🟢
        "restart": 0xFFFF00, # 🟡
        "stop": 0xFF0000     # 🔴
    }

    # Turn green if AAF rename succeeded
    if action == "restart" and nbspaces and ENABLE_AAF:
        color = 0x00FF00
    else:
        color = colors.get(action, 0x808080)

    # Embed fields
    fields = [
        {"name": "Container", "value": f"`{container_name}`", "inline": True},
        {"name": "User", "value": f"{user.name} ({user.id})", "inline": True},
        {"name": "Server Host", "value": f"`{HOSTNAME}`", "inline": False},
    ]

    # Only include AAF info if enabled and returned
    if ENABLE_AAF and (nbspaces or gameline):
        fields.append({"name": "Non-breaking Spaces Written", "value": str(nbspaces), "inline": True})
        fields.append({"name": "Game Name Line", "value": gameline or "(not found)", "inline": False})

    embed = {
        "title": f"Container {action.capitalize()} Executed",
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": fields,
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
        await interaction.response.send_message(
            "❌ You don't have permission to do that.", ephemeral=True
        )
        return

    # 👇 acknowledge right away (avoids 404 Unknown Interaction)
    await interaction.response.defer(ephemeral=True)

    try:
        nbspaces, gameline = call_external_script(container)   # may take time
        run_docker_cmd(["restart", container])                 # may take time
        send_webhook(interaction.user, container, "restart", nbspaces, gameline)

        # 👇 final follow-up message after long tasks finish
        await interaction.followup.send(
            f"🟡 Restarted `{container}` successfully.", ephemeral=True
        )

    except Exception as e:
        # use follow-up, not response, because we already deferred
        await interaction.followup.send(
            f"⚠️ Error restarting `{container}`: {e}", ephemeral=True
        )

@tree.command(name="stop", description="Stop a Docker container by name")
@app_commands.describe(container="The name of the Docker container to stop")
async def stop(interaction: discord.Interaction, container: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("❌ You don't have permission to do that.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        run_docker_cmd(["stop", container])
        send_webhook(interaction.user, container, "stop")
        await interaction.followup.send(f"🔴 Stopped `{container}` successfully.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Error stopping `{container}`: {e}", ephemeral=True)

@tree.command(name="start", description="Start a Docker container by name")
@app_commands.describe(container="The name of the Docker container to start")
async def start(interaction: discord.Interaction, container: str):
    if not is_authorized(interaction):
        await interaction.response.send_message("❌ You don't have permission to do that.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        # ✅ Call the external rename script before starting
        nbspaces, gameline = call_external_script(container)
        run_docker_cmd(["start", container])
        send_webhook(interaction.user, container, "start", nbspaces, gameline)
        await interaction.followup.send(f"🟢 Started `{container}` successfully.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Error starting `{container}`: {e}", ephemeral=True)

# --- AUTOCOMPLETE ---
@restart.autocomplete("container")
@stop.autocomplete("container")
async def running_container_autocomplete(interaction: discord.Interaction, current: str):
    containers = get_containers(only_running=True)
    choices = []
    for name, status in containers:
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=f"{name} ({status})", value=name))
    return choices[:25]

@start.autocomplete("container")
async def stopped_container_autocomplete(interaction: discord.Interaction, current: str):
    containers = get_containers(only_stopped=True)
    choices = []
    for name, status in containers:
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=f"{name} ({status})", value=name))
    return choices[:25]

# --- RUN BOT ---
bot.run(TOKEN)
