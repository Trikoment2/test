import logging
import os
import requests

from telegram import constants
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, \
InlineQueryHandler, Application
from telegram import Filters
from telegram.ext.updater import Updater
from pydub import AudioSegment
from openai_helper import OpenAIHelper
from flask import Flask, request

app = Flask(__name__)

TOKEN = 'your_bot_token_here'
URL = f'https://api.telegram.org/bot{TOKEN}/'


class ChatGPT3TelegramBot:
    """
    Class representing a Chat-GPT3 Telegram Bot.
    """

    def __init__(self, config, openai):
        self.config = config
        self.openai = openai
        self.bot = telegram.Bot(token=self.config['telegram_token'])
        self.updater = Updater(token=self.config['telegram_token'], use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.dispatcher.add_handler(CommandHandler('start', self.start))
        self.dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), self.reply))
        self.commands = [
            BotCommand(command='help', description='Show this help message'),
            BotCommand(command='reset', description='Reset the conversation'),
            BotCommand(command='image', description='Generate image from prompt (e.g. /image cat)'),
            BotCommand(command='getupdates', description='Get the latest updates from the Telegram server')
        ]
        self.dispatcher.add_handler(CommandHandler('help', self.help_command))
        self.dispatcher.add_handler(CommandHandler('reset', self.reset_command))
        self.dispatcher.add_handler(CommandHandler('image', self.image_command))
        self.dispatcher.add_handler(CommandHandler('getupdates', self.get_updates_command))
        self.updater.start_polling()

    def start(self, update, context):
        context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me!")

    def reply(self, update, context):
        message = update.message.text
        response = self.openai.generate_response(message)
        context.bot.send_message(chat_id=update.effective_chat.id, text=response)

    def help_command(self, update, context):
        message = "Available commands:\n\n"
        for command in self.commands:
            message += f"/{command.command} - {command.description}\n"
        context.bot.send_message(chat_id=update.effective_chat.id, text=message)

    def reset_command(self, update, context):
        # Reset the conversation here
        context.bot.send_message(chat_id=update.effective_chat.id, text="Conversation reset!")

    def image_command(self, update, context):
        # Generate image from prompt here
        context.bot.send_message(chat_id=update.effective_chat.id, text="Image generated!")

    def get_updates_command(self, update, context):
        # Get the latest updates from the Telegram server here
        context.bot.send_message(chat_id=update.effective_chat.id, text="Updates received!")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the help menu.
        """
        commands = [f'/{command.command} - {command.description}' for command in self.commands]
        help_text = 'I\'m a ChatGPT bot, talk to me!' + \
                    '\n\n' + \
                    '\n'.join(commands) + \
                    '\n\n' + \
                    'Send me a voice message or file and I\'ll transcribe it for you!' + \
                    '\n\n' + \
                    "Open source at https://github.com/n3d1117/chatgpt-telegram-bot"
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    async def get_updates(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Get the latest updates from the Telegram server.
        """
        logging.info('Getting updates...')
        updates = context.bot.get_updates()
        for update in updates:
            logging.debug(f'Received update: {update}')

    def run(self) -> None:
        """
        Runs the bot.
        """
        application = ApplicationBuilder(constants.TELEGRAM_BOT_API_URL, self.config['bot_token']) \
            .add_handler(CommandHandler('help', self.help)) \
            .add_handler(CommandHandler('reset', self.reset)) \
            .add_handler(CommandHandler('image', self.generate_image)) \
            .add_handler(CommandHandler('getupdates', self.get_updates)) \
            .add_handler(MessageHandler(filters.voice, self.handle_voice_message)) \
            .add_handler(MessageHandler(filters.audio, self.handle_audio_message)) \
            .add_handler(InlineQueryHandler(self.inline_query)) \
            .set_default_handler(self.handle_unknown_message) \
            .build()
        logging.info('Starting bot...')
        application.run()

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resets the conversation.
        """
        if not await self.is_allowed(update):
            logging.warning(f'User {update.message.from_user.name} is not allowed to reset the conversation')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'Resetting the conversation for user {update.message.from_user.name}...')

        chat_id = update.effective_chat.id
        self.openai.reset_chat_history(chat_id=chat_id)
        await context.bot.send_message(chat_id=chat_id, text='Done!')

    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Generates an image for the given prompt using DALL·E APIs
        """
        if not await self.is_allowed(update):
            logging.warning(f'User {update.message.from_user.name} is not allowed to generate images')
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        image_query = update.message.text.replace('/image', '').strip()
        if image_query == '':
            await context.bot.send_message(chat_id=chat_id, text='Please provide a prompt! (e.g. /image cat)')
            return

        logging.info(f'New image generation request received from user {update.message.from_user.name}')

        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.UPLOAD_PHOTO)
        try:
            image_url = self.openai.generate_image(prompt=image_query)
            await context.bot.send_photo(
                chat_id=chat_id,
                reply_to_message_id=update.message.message_id,
                photo=image_url
            )
        except Exception as e:
            logging.exception(e)
            await context.bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=update.message.message_id,
                text=f'Failed to generate image: {str(e)}'
            )

    async def transcribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Transcribe audio messages.
        """
        if not await self.is_allowed(update):
            logging.warning(f'User {update.message.from_user.name} is not allowed to transcribe audio messages')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'New transcribe request received from user {update.message.from_user.name}')

        chat_id = update.effective_chat.id
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)

        if update.message.voice:
            filename = update.message.voice.file_unique_id
        elif update.message.audio:
            filename = update.message.audio.file_unique_id
        elif update.message.video:
            filename = update.message.video.file_unique_id
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text='Unsupported file type'
            )
            return

        filename_mp3 = f'{filename}.mp3'

        try:
            if update.message.voice:
                media_file = await context.bot.get_file(update.message.voice.file_id)

            elif update.message.audio:
                media_file = await context.bot.get_file(update.message.audio.file_id)

            elif update.message.video:
                media_file = await context.bot.get_file(update.message.video.file_id)

            await media_file.download_to_drive(filename)
            
            audio_track = AudioSegment.from_file(filename)
            audio_track.export(filename_mp3, format="mp3")

            # Transcribe the audio file
            transcript = self.openai.transcribe(filename_mp3)

            if self.config['voice_reply_transcript']:
                # Send the transcript
                await context.bot.send_message(
                    chat_id=chat_id,
                    reply_to_message_id=update.message.message_id,
                    text=f'_Transcript:_\n"{transcript}"',
                    parse_mode=constants.ParseMode.MARKDOWN
                )
            else:
                # Send the response of the transcript
                response = self.openai.get_chat_response(chat_id=chat_id, query=transcript)
                await context.bot.send_message(
                    chat_id=chat_id,
                    reply_to_message_id=update.message.message_id,
                    text=f'_Transcript:_\n"{transcript}"\n\n_Answer:_\n{response}',
                    parse_mode=constants.ParseMode.MARKDOWN
                )
        except Exception as e:
            logging.exception(e)
            await context.bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=update.message.message_id,
                text=f'Failed to transcribe text: {str(e)}'
            )
        finally:
            # Cleanup files
            if os.path.exists(filename_mp3):
                os.remove(filename_mp3)
            if os.path.exists(filename):
                os.remove(filename)

    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        React to incoming messages and respond accordingly.
        """
        if not await self.is_allowed(update):
            logging.warning(f'User {update.message.from_user.name} is not allowed to use the bot')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'New message received from user {update.message.from_user.name}')
        chat_id = update.effective_chat.id

        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        response = self.openai.get_chat_response(chat_id=chat_id, query=update.message.text)
        await context.bot.send_message(
            chat_id=chat_id,
            reply_to_message_id=update.message.message_id,
            text=response,
            parse_mode=constants.ParseMode.MARKDOWN
        )

    async def inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle the inline query. This is run when you type: @botusername <query>
        """
        query = update.inline_query.query

        if query == "":
            return

        results = [
            InlineQueryResultArticle(
                id=query,
                title="Ask ChatGPT",
                input_message_content=InputTextMessageContent(query),
                description=query,
                thumb_url='https://user-images.githubusercontent.com/11541888/223106202-7576ff11-2c8e-408d-94ea-b02a7a32149a.png'
            )
        ]

        await update.inline_query.answer(results)

    async def send_disallowed_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Sends the disallowed message to the user.
        """
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=self.disallowed_message,
            disable_web_page_preview=True
        )

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handles errors in the telegram-python-bot library.
        """
        logging.debug(f'Exception while handling an update: {context.error}')

    def is_group_chat(self, update: Update) -> bool:
        """
        Checks if the message was sent from a group chat
        """
        return update.effective_chat.type in [
            constants.ChatType.GROUP,
            constants.ChatType.SUPERGROUP
        ]

    async def is_user_in_group(self, update: Update, user_id: int) -> bool:
        """
        Checks if user_id is a member of the group
        """
        member = await update.effective_chat.get_member(user_id)
        return member.status in [
            constants.ChatMemberStatus.OWNER,
            constants.ChatMemberStatus.ADMINISTRATOR,
            constants.ChatMemberStatus.MEMBER
        ]

    async def is_allowed(self, update: Update) -> bool:
        """
        Checks if the user is allowed to use the bot.
        """
        if self.config['allowed_user_ids'] == '*':
            return True

        allowed_user_ids = self.config['allowed_user_ids'].split(',')

        # Check if user is allowed
        if str(update.message.from_user.id) in allowed_user_ids:
            return True

        # Check if it's a group a chat with at least one authorized member
        if self.is_group_chat(update):
            for user in allowed_user_ids:
                if await self.is_user_in_group(update, user):
                    logging.info(f'{user} is a member. Allowing group chat message...')
                    return True
            logging.info(f'Group chat messages from user {update.message.from_user.name} are not allowed')

        return False

    async def post_init(self, application: Application) -> None:
        """
        Post initialization hook for the bot.
        """
        await application.bot.set_my_commands(self.commands)

    def run(self):
        """
        Runs the bot indefinitely until the user presses Ctrl+C
        """
        application = ApplicationBuilder() \
            .token(self.config['token']) \
            .proxy_url(self.config['proxy']) \
            .get_updates_proxy_url(self.config['proxy']) \
            .post_init(self.post_init) \
            .build()

        application.add_handler(CommandHandler('reset', self.reset))
        application.add_handler(CommandHandler('help', self.help))
        application.add_handler(CommandHandler('image', self.image))
        application.add_handler(CommandHandler('start', self.help))
        application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO, self.transcribe))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.prompt))
        application.add_handler(InlineQueryHandler(self.inline_query, chat_types=[
            constants.ChatType.GROUP, constants.ChatType.SUPERGROUP
        ]))

        application.add_error_handler(self.error_handler)

        application.run_polling()
import requests
import datetime
import time

TOKEN = "<YOUR BOT TOKEN>" # replace with your bot token
URL = "https://api.telegram.org/bot{}/".format(TOKEN)

def get_url(url):
    response = requests.get(url)
    content = response.content.decode("utf8")
    return content

def get_json_from_url(url):
    content = get_url(url)
    js = json.loads(content)
    return js

def get_updates(offset=None):
    url = URL + "getUpdates?timeout=100"
    if offset:
        url += "&offset={}".format(offset)
    js = get_json_from_url(url)
    return js

def send_message(text, chat_id):
    url = URL + "sendMessage?text={}&chat_id={}".format(text, chat_id)
    get_url(url)

def main():
    last_update_id = None
    while True:
        print("getting updates")
        updates = get_updates(last_update_id)
        if len(updates["result"]) > 0:
            last_update_id = updates["result"][-1]["update_id"] + 1
            for update in updates["result"]:
                try:
                    text = update["message"]["text"]
                    chat_id = update["message"]["chat"]["id"]
                    send_message(text, chat_id)
                except Exception as e:
                    print(e)
        time.sleep(0.5)

if __name__ == '__main__':
    main()
