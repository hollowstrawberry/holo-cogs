# 🍓 holo-cogs

This is the place for my cogs that were too spicy to go into [crab-cogs](https://github.com/hollowstrawberry/crab-cogs). Mostly AI stuff.

These require Red 3.5+ and Python 3.10+

### Installation

To add one of these cogs to your instance of Red, send the following commands one by one (`[p]` is your prefix):
```
[p]load downloader
[p]repo add holo-cogs https://github.com/hollowstrawberry/holo-cogs
[p]cog install holo-cogs [cog name]
[p]load [cog name]
```

You may be prompted to respond with "I agree" after the second command.

&nbsp;

## 🤖 Agent

The `agent` cog will let you use your Discord Bot as an artificial user in your server. More than just a chat bot, it's designed with division of work (sub-agents), it features text-based memories, and has access to many tools like web search, image generation, and voice generation. Its memories may be manually set or automatically created by the bot, and will be recalled according to the context of the conversation.

I have found that the more context you provide to an LLM, the more natural its responses will be, and this is consistent with observations of developments in local agents such as OpenClaw. If you're using this cog, I leave it all to you, and I hope you and/or your community enjoy it.

⚠️ **Important:** You are using this cog at your own risk. Like all AI software right now, this is experimental. There may be **limited safeguards against abuse**. Depending on which LLM provider you choose, responses may be costly (a fraction of a cent to several cents each), on top of any possible image generation and/or voice generation. Token limits for different features are customizable.

⚠️ **User data:** This cog sends recent messages in a channel to chosen LLM providers. It may also store text-based memories containing information about users and past conversations, in certain conditions defined by the bot owner. The bot owner becomes wholly responsible for the data of its users.

⚠️ The `agent` cog currently needs better documentation.

&nbsp;

## 🖼️ Arcenciel

Image generation using my website's api, [arcenciel.io](https://arcenciel.io). Not really useful for public use.
