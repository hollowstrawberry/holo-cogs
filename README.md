# 🍓 holo-cogs

This is the place for my cogs that were too spicy to go into [crab-cogs](https://github.com/hollowstrawberry/crab-cogs)

These require Red 3.5, which uses the newer Discord interaction features.

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

## 🎶 audioplayer

Live audio player, updates every 10 seconds, or when the song changes, or you press a button. Stays at the bottom of the designated chat for as long as there is audio playing.

⚠️ Discord doesn't like when things update periodically without human input, even when it follows ratelimits, so use this at your own risk.

![image](https://github.com/user-attachments/assets/7c77467c-7cac-4dac-a02e-ca06b9f296b5)

## 🚪 gptwelcome

Uses AI to give a unique welcome message for each new user, according to their username and avatar. This may sound useless at first, but generic welcome messages are often ignored, and even something simple as saying something unique about someone may catch their attention. The prompt is customizable, you should read the default one first. Make sure your server has welcome messages enabled, as the bot replies to those.

⚠️ Utilizes the OpenAI API, which will cost you a fraction of a penny every time it is used.

![image](https://github.com/user-attachments/assets/46c8e4a8-7cc7-4ff6-b864-5ee132c7ec6c)

## 🤖 gptmemory

The gptmemory cog will let you use your Discord Bot as an artificial user in your server, typically serving as an assistant. It features memories which may be manually set or automatically created by the bot, and recalled according to the context of the conversation. It is also capable of viewing images and using the internet. I have found that the more context you provide to a chatbot agent, the more natural its responses will be. Because of the additional context that memories provide, me and my peers have had a lot of fun with this cog.

A new feature allows it to interface with [bz-cogs](https://github.com/zhaobenny/bz-cogs)'s aimage and [crab-cogs](https://github.com/hollowstrawberry/crab-cogs)'s imagescanner to generate and revise Stable Diffusion images using natural conversation.

⚠️ This cog is **not meant for public use**, you are using it at your own risk. There are **no safeguards against abuse**. It uses GPT models, and the approach to memory means it will use up to 3 times as many input tokens as you would expect, so you may face large monetary charges to your OpenAI api project (I pay around 20 dollars a month after frequent use from many users). It does however let you customize the different limits of tokens used by the bot. It also optionally stores text-based "memories" which may contain information about users. Some of these memories as well as the recent chat logs are sent to OpenAI servers every time the bot user is pinged. As the bot's owner you become wholly responsible for the data of your users and the money that user interactions will consume.
