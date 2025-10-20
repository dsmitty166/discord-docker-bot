# discord-docker-bot
Discord bot to reboot docker containers

Execute with:

docker run -d \
  --name discord-docker-bot \
  --env-file .env \
  -v /var/run/docker.sock:/var/run/docker.sock \
  ghcr.io/dsmitty166/discord-docker-bot:latest
