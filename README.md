# 🤖 Discord Message Counter Bot

A Discord bot that silently counts messages in any channel and fires a custom message when a threshold is hit. Everything — the channel, the count, and the phrase — is fully customizable via bot commands or by editing `config.json` directly.

---

## ⚡ Quick Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your Discord bot
1. Go to https://discord.com/developers/applications
2. Click **New Application** → give it a name → **Create**
3. Go to **Bot** → **Add Bot**
4. Under **Privileged Gateway Intents**, enable:
   - ✅ **Message Content Intent**
   - ✅ **Server Members Intent** (optional but recommended)
5. Copy the **Token** (click "Reset Token" if needed)

### 3. Configure the bot
Open `config.json` and paste your token:
```json
{
  "bot_token": "PASTE_YOUR_TOKEN_HERE",
  "triggers": []
}
```

### 4. Invite the bot to your server
In the Developer Portal → **OAuth2 → URL Generator**:
- Scopes: `bot`
- Bot Permissions: `Send Messages`, `Read Message History`, `View Channels`

Open the generated URL and add the bot to your server.

### 5. Run the bot
```bash
python bot.py
```

---

## 🎮 Commands

All commands require **Administrator** permission except `!bothelp`.

| Command | Description |
|---|---|
| `!addtrigger #channel N your message` | Add a trigger: posts *your message* every N messages in *#channel* |
| `!removetrigger #channel` | Remove the trigger for a channel |
| `!edittrigger #channel N new message` | Update count or message for an existing trigger |
| `!listtriggers` | Show all triggers with live progress counters |
| `!resetcounter #channel` | Manually reset a channel's counter to 0 |
| `!bothelp` | Show command list |

### Examples
```
!addtrigger #general 50 🎉 Wow, 50 messages! Keep the conversation going!
!addtrigger #announcements 10 📢 10 messages milestone reached!
!edittrigger #general 100 💯 One hundred messages! Amazing community!
!listtriggers
!removetrigger #general
!resetcounter #general
```

---

## ⚙️ config.json structure

You can also add triggers manually by editing `config.json`:

```json
{
  "bot_token": "YOUR_TOKEN",
  "triggers": [
    {
      "channel_id": 123456789012345678,
      "message_count": 50,
      "custom_message": "🎉 50 messages! This chat is on fire!"
    },
    {
      "channel_id": 987654321098765432,
      "message_count": 100,
      "custom_message": "💯 100 messages in #support — great engagement!"
    }
  ]
}
```

To find a channel ID: right-click a channel in Discord → **Copy Channel ID** (requires Developer Mode: User Settings → Advanced → Developer Mode ✅).

---

## 📝 Notes

- **Counters reset** to 0 after each trigger fires, then start counting again.
- **Bot messages are ignored** — only human messages count.
- **Multiple channels** can each have their own independent trigger.
- **Each channel supports one trigger** at a time. Edit it with `!edittrigger` to change.
- Counters are **in-memory** and reset when the bot restarts. The triggers/config persist in `config.json`.
