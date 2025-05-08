#!/bin/bash
docker build -t wget-web .
docker run -d -e APP_PASSWORD="YOUR_PASSWORD" -e DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/" --network=proxy -v /your/download/folder:/downloads -v $(pwd):/app --name wget-ui wget-web
