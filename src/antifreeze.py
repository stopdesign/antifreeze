import asyncio
import logging
import signal
import socket
import time
from datetime import datetime

import coloredlogs
from aiogram import Bot, Dispatcher
from aiogram.types import Message, ParseMode
from aiogram.utils.markdown import hpre
from dbus_fast import BusType
from dbus_fast.aio.message_bus import MessageBus
from settings import app_config

# Enable logging
fmt = "%(asctime).19s • %(levelname).1s • %(name)s • %(message)s"
coloredlogs.install("INFO", fmt=fmt)
log = logging.getLogger("antifreeze")


TG_TOKEN = app_config.telegram.token

IBC_COMMAND_HOST = app_config.ibc.host
IBC_COMMAND_PORT = app_config.ibc.port


async def service_status():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    name = "org.freedesktop.systemd1"
    path = "/org/freedesktop/systemd1"
    introspection = await bus.introspect(name, path)
    obj = bus.get_proxy_object(name, path, introspection)
    manager = obj.get_interface(f"{name}.Manager")
    unit = await manager.call_load_unit("ibgw.service")  # type: ignore
    obj = bus.get_proxy_object(name, unit, introspection)
    prop = obj.get_interface("org.freedesktop.DBus.Properties")
    state = await prop.call_get(f"{name}.Unit", "ActiveState")  # type: ignore
    print(state.value)


async def restart_ibgw():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    name = "org.freedesktop.systemd1"
    path = "/org/freedesktop/systemd1"
    introspection = await bus.introspect(name, path)
    obj = bus.get_proxy_object(name, path, introspection)
    manager = obj.get_interface(f"{name}.Manager")
    job = await manager.call_restart_unit("ibgw.service", "fail")  # type: ignore
    print(job)


def ibc_reconnect_data():
    status = "NOTHING"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)

    try:
        sock.connect((IBC_COMMAND_HOST, IBC_COMMAND_PORT))
        sock.sendall(b"RECONNECTDATA\n")
        status = sock.recv(1024).decode("utf-8")
        sock.sendall(b"EXIT\n")
    except Exception as e:
        status = f"Error: {e}"
    finally:
        sock.close()

    return status


def ibc_info():
    status = "NOTHING"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)

    try:
        sock.connect((IBC_COMMAND_HOST, IBC_COMMAND_PORT))
        sock.sendall(b"INFO\n")
        status = sock.recv(1024).decode("utf-8")
        sock.sendall(b"EXIT\n")
    except Exception as e:
        status = f"Error: {e}"
    finally:
        sock.close()

    return status


class AntifreezeBot:
    bot: Bot
    dp: Dispatcher

    def __init__(self, loop) -> None:
        self.user_id = None
        self.loop = loop

        self.bot = Bot(token=TG_TOKEN, loop=loop, parse_mode=ParseMode.HTML)
        self.dp = Dispatcher(self.bot, loop=loop)

        self.dp.register_errors_handler(self.errors_handler)

        mh = self.dp.register_message_handler

        mh(self.tg_kill, commands=["kill"])
        mh(self.tg_info, commands=["info"])
        mh(self.tg_start, commands=["start"])
        mh(self.tg_restart_gw, commands=["restartgw"])
        mh(self.tg_reconnect_data, commands=["rd"])

    async def errors_handler(self, update, exception):
        log.error(f"Bot exception: {exception} | {update}")

    async def tg_start(self, message: Message):
        self.user_id = message.from_user.id
        log.info(f"Start spamming user_id {self.user_id}")

    async def tg_restart_gw(self, message: Message):
        await message.answer_chat_action("typing")
        await restart_ibgw()
        log.info(f"gw restarted")
        await message.answer(f"Ok")

    async def tg_kill(self, message: Message):
        await message.answer_dice()
        # if message.chat.id != TG_CHAT_ID:
        #     return
        log.warning("Kill signal")
        await message.answer("STOP")
        # чтобы сообщение отметилось как обработанное
        await asyncio.sleep(2)

    async def tg_reconnect_data(self, message: Message):
        await message.answer_chat_action("typing")
        res = ibc_reconnect_data()
        await message.answer(f"Result: {res}")

    async def tg_info(self, message: Message):
        log.info(f"Account info requested")
        await message.answer_chat_action("typing")
        await asyncio.sleep(1)
        res = ibc_info()
        await message.answer(res)
        # txt = f"Net Value:   10\n" f"Margin Used: 20\n"
        # await message.answer(f"{hpre(txt)}")

    async def start_tg_bot(self):
        log.info("Start bot")
        await self.dp.start_polling()

    async def _stop_bot(self):
        if session := await self.bot.get_session():
            await session.close()

    async def send_time(self, msg: str):
        if self.user_id:
            await self.bot.send_message(self.user_id, msg)

    def stop_bot(self):
        self.loop.create_task(self._stop_bot())


class Tester:
    def __init__(self, bot) -> None:
        self.run = True
        self.bot = bot

    def stop(self) -> None:
        self.run = False

    async def handler(self):
        # Loop forever, checking the system time every second
        prev_dt = time.monotonic()
        while self.run:
            if time.monotonic() - prev_dt > 600:
                prev_dt = time.monotonic()
                now = datetime.now()
                print(f"Current time: {now.strftime('%H:%M:%S')}")
                await self.bot.send_time(now.strftime("%H:%M:%S"))
            await asyncio.sleep(0.01)
        log.error("TESTER OUT")


async def main():
    # Set up the asyncio event loop and tasks
    loop = asyncio.get_running_loop()

    ab = AntifreezeBot(loop)
    tester = Tester(ab)

    bot_task = loop.create_task(ab.start_tg_bot())
    time_task = loop.create_task(tester.handler())

    def signal_handler():
        print()
        tester.stop()
        ab.stop_bot()
        bot_task.cancel()

    # Add a signal handler to catch the KeyboardInterrupt signal
    loop.add_signal_handler(signal.SIGINT, signal_handler)

    # Run the event loop until either task completes
    await asyncio.gather(bot_task, time_task)


if __name__ == "__main__":
    asyncio.run(main())
