# ChannelActionsBot (Python Version)

This is a Python conversion of the [ChannelActionsBot](https://github.com/xditya/ChannelActionsBot) originally written in TypeScript/Deno.

## Features
- Auto-approve or auto-decline join requests for channels and groups.
- Custom welcome messages for approved/declined members.
- Multi-language support using Fluent (.ftl) files.
- Admin settings via private chat.
- Owner tools: Stats and Broadcast.
- Ready for hosting on Render.

## Environment Variables
The following environment variables are required:
- `BOT_TOKEN`: Your Telegram Bot Token from @BotFather.
- `OWNERS`: Space-separated list of owner Telegram IDs.
- `MONGO_URL`: Your MongoDB connection string.
- `PORT`: (Optional) Port for the health check server (default: 8080).

## Hosting on Render
1. Create a new **Web Service** on Render.
2. Connect your repository.
3. Select **Python** as the runtime.
4. Set the **Build Command** to `pip install -r requirements.txt`.
5. Set the **Start Command** to `gunicorn main:app`.
6. Add the required environment variables in the Render dashboard.

## Local Setup
1. Clone the repository.
2. Install dependencies: `pip install -r requirements.txt`.
3. Create a `.env` file with the required variables.
4. Run the bot: `python main.py`.
