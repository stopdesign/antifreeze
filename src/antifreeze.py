import asyncio
import json
import logging
import signal
import re
import socket
from datetime import datetime
from time import monotonic, sleep

import coloredlogs
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, Text
from aiogram.types import KeyboardButton, Message
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.utils.markdown import hpre
from dbus_fast import BusType
from dbus_fast.aio.message_bus import MessageBus
from ib_sync import IBSync, IBThread

from settings import app_config

# Enable logging
fmt = "%(asctime).19s • %(levelname).1s • %(name)s • %(message)s"
coloredlogs.install("INFO", fmt=fmt)
log = logging.getLogger("antifreeze")


"""
BotFather commands:
re_data - Reconnect data
re_acc - Reconnect account
ibc_restart - Restart gateway with IBC
gw_start - Start gateway sevice
gw_stop - Stop gateway sevice
"""


TG_TOKEN = app_config.telegram.token

IBC_HOST = app_config.ibc.host
IBC_PORT = app_config.ibc.port

GATEWAY_HOST = app_config.gateway.host
GATEWAY_PORT = app_config.gateway.port
CLIENT_ID = 999



async def ibgw_status():
    ib = IBSync()
    ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=CLIENT_ID)

    txt = ""
    try:
        IBThread(ib).start()

        dt = monotonic()
        while not sleep(0.01) and monotonic() - dt < 3:
            if ib.nextValidOrderId > 0:
                break

        if not ib.nextValidOrderId > 0:
            return "Not connected"

        fields, positions = ib.get_account_info()
        net_value = float(fields.get("NetLiquidation", "nan"))
        margin_used = float(fields.get("MaintMarginReq", "nan"))
        realized_pnl = float(fields.get("RealizedPnL", "nan"))
        unrealized_pnl = float(fields.get("UnrealizedPnL", "nan"))
        cushion = float(fields.get("Cushion", "nan"))

        txt = ""
        txt += f"Account       {ib.account_id:>12}\n"
        txt += "==========================\n"
        txt += "\n"

        txt += f"Net Value       {net_value:10.2f}\n"
        txt += f"Margin          {margin_used:10.2f}\n"
        txt += f"Cushion         {cushion:10.2f}\n"
        txt += f"Unrlzd PnL      {unrealized_pnl:+10.2f}\n"

        txt += "\n"
        txt += "              Pos      PnL\n"
        txt += "--------------------------\n"

        for p in positions:
            sid = ib.sid_for_contract(p.contract)
            msid = sid.split("_", 1)[1]
            amnt = float(p.amount)
            pnl = float(p.unrealized_pnl)
            txt += f"{msid:<9}{amnt:+8.0f}{pnl:+9.2f}\n"

        txt = txt.replace("+nan", "   ·")
        txt = txt.replace(" nan", "   ·")
        txt = txt.strip().replace(" ", " ") + "\n⠀"

    except Exception as e:
        txt = f"ERROR: {e}"

    finally:
        ib.disconnect()

    return txt


async def service_status():
    txt = ""

    # FIXME: взять из конфига
    services = {
        "Gateway-l": "ibgw-life.service",
        "Gateway-p": "ibgw-paper.service",
#        "Trading Bot": "trading.service",
    }

    txt += "\nServices\n==========================\n"
    for title, service in services.items():
        try:
            status = await get_service_status(service)
        except:
            status = "--"
        txt += f"{title:<12}{status:>14}\n"

    # log.info(f"Services {txt}")

    # запросить INFO через телнет, отформатировать
    ibc_info = ibc_run_command(IBC_HOST, IBC_PORT, "INFO")
    gateway, market, hist, retry = parse_ibs_status(ibc_info)

    # log.info(f"Services {ibc_info}")

    txt += "\nGateway\n==========================\n"
    for key, val in gateway.items():
        txt += f"{key:<12}{val:>14}\n"

    txt += "\nMarket data\n==========================\n"
    for key, val in market.items():
        txt += f"{key:<12}{val:>14}\n"

    txt += "\nHistorical data\n==========================\n"
    for key, val in hist.items():
        txt += f"{key:<12}{val:>14}\n"

    return txt.strip().replace(" ", " ") + "\n⠀", retry


async def get_service_status(service_name: str) -> str:
    # получить статусы сервисов systemctl (названия в конфиге)
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    name = "org.freedesktop.systemd1"
    path = "/org/freedesktop/systemd1"
    introspection = await bus.introspect(name, path)
    obj = bus.get_proxy_object(name, path, introspection)
    manager = obj.get_interface(f"{name}.Manager")
    unit = await manager.call_load_unit(service_name)  # type: ignore
    obj = bus.get_proxy_object(name, unit, introspection)
    prop = obj.get_interface("org.freedesktop.DBus.Properties")
    state = await prop.call_get(f"{name}.Unit", "ActiveState")  # type: ignore
    return str(state.value)


def parse_ibs_status(status: str) -> tuple[dict, dict, dict, list]:
    gateway, market, historical, retry = {}, {}, {}, []

    try:
        s = json.loads(status.replace("OK {", "{"))
    except Exception as e:
        log.error("Error parsing json from INFO")
        log.exception(e)
        return gateway, market, historical, retry

    con = s.get("connections", {})

    gateway["IBC auth"] = s.get("ibc_login", "--")
    gateway["API Server"] = con.get("Interactive Brokers API Server", "--")
    gateway["API Clients"] = con.get("API Client", "--")

    market = parse_on_off(con.get("Market Data Farm", ""))
    historical = parse_on_off(con.get("Historical Data Farm", ""))

    retry = s.get("reconnecting", [])

    return gateway, market, historical, retry


def parse_on_off(value: str) -> dict:
    """
    Парсит вот такое говно:
    "ON: uscrypto, usfuture  OFFusfarm"
    "Inactive: ushmds"
    """
    value = value.replace(" OFF", " OFF ")
    value = value.replace(":", " ").replace(",", " ")
    value = re.sub(r"\s+", " ", value).strip()

    status = "--"
    res = {}

    for token in value.lower().split():
        if token == "on":
            status = "OK"
        elif token == "disconnected":
            status = token
        elif token == "inactive":
            status = token
        else:
            res[token] = str(status)

    return res


async def stop_ibgw(service_name: str):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    name = "org.freedesktop.systemd1"
    path = "/org/freedesktop/systemd1"
    introspection = await bus.introspect(name, path)
    obj = bus.get_proxy_object(name, path, introspection)
    manager = obj.get_interface(f"{name}.Manager")
    job = await manager.call_restart_unit(service_name, "fail")  # type: ignore
    print(job)


def ibc_run_command(host, port, command):
    """
    Подключается в сокет канала управления IBC,
    отправляет команду, читает ответ.
    """
    status = ""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)

    try:
        sock.connect((host, port))
        sock.sendall((f"{command}\n").encode())
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

        rr = Router()
        self.dp = Dispatcher()
        self.dp.include_router(rr)
        self.bot = Bot(token=TG_TOKEN, parse_mode="HTML")

        # self.dp.register_errors_handler(self.errors_handler)

        rr.message.register(self.tg_start, Command(commands=["start"]))

        rr.message.register(self.tg_account_info, Text(text="💰 Account⠀"))
        rr.message.register(self.tg_service_info, Text(text="🚀 Service⠀"))

        rr.message.register(self.tg_gw_stop, Command(commands=["gw_stop"]))

        rr.message.register(self.tg_reconnect_account, Command(commands=["re_acc"]))
        rr.message.register(self.tg_reconnect_data, Command(commands=["re_data"]))
        rr.message.register(self.tg_ibc_restart, Command(commands=["ibc_restart"]))

        self.dp.startup.register(self.on_startup)

    async def on_startup(self, bot: Bot):
        print("ON STARTUP", bot)

    async def typing(self, message: Message):
        await self.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    async def start_polling(self):
        log.info("Start bot")
        await self.dp.start_polling(self.bot, skip_updates=True, handle_signals=False)

    async def _stop_bot(self):
        await self.dp.stop_polling()

    def stop_bot(self):
        self.loop.create_task(self._stop_bot())

    async def errors_handler(self, update, exception):
        log.error(f"Bot exception: {exception} | {update}")

    ##################################################
    # Подключение бота

    async def tg_start(self, message: Message):
        self.user_id = message.from_user.id
        log.info(f"Start spamming user_id {self.user_id}")

        builder = ReplyKeyboardBuilder()
        builder.row(
            KeyboardButton(text="🚀 Service⠀"),
            KeyboardButton(text="💰 Account⠀"),
        )
        markup = builder.as_markup(is_persistent=True, resize_keyboard=True)

        await message.reply("Start", reply_markup=markup)

    ##################################################
    ## Команды IBC

    async def tg_reconnect_data(self, message: Message):
        await self.typing(message)
        res = ibc_run_command(IBC_HOST, IBC_PORT, "RECONNECTDATA")
        await message.answer(f"Result: {res}")

    async def tg_reconnect_account(self, message: Message):
        await self.typing(message)
        res = ibc_run_command(IBC_HOST, IBC_PORT, "RECONNECTACCOUNT")
        await message.answer(f"Result: {res}")

    async def tg_ibc_restart(self, message: Message):
        await self.typing(message)
        res = ibc_run_command(IBC_HOST, IBC_PORT, "RESTART")
        await message.answer(f"Result: {res}")

    ##################################################
    ## Команды Systemd

    async def tg_gw_stop(self, message: Message):
        await self.typing(message)
        await stop_ibgw("ibgw-paper.service")  # FIXME: убрать хардкодинг
        log.info(f"gw stopped")
        await message.answer(f"Ok")


    ##################################################
    ## Запрос информации

    async def tg_account_info(self, message: Message):
        log.info(f"Account info requested")
        await self.typing(message)
        res = await ibgw_status()
        await message.answer(f"{hpre(res)}")

    async def tg_service_info(self, message: Message):
        log.info(f"Service info requested")
        await self.typing(message)

        res, retries = await service_status()
        await message.answer(f"{hpre(res)}")

        # Сообщения вида "Attempt 6: connection error..."
        for retry in retries:
            await message.answer(retry)

    ##################################################

    async def send_time(self, msg: str):
        if self.user_id:
            try:
                await self.bot.send_message(self.user_id, msg)
            except Exception as e:
                log.error(e)


class Tester:
    def __init__(self, bot) -> None:
        self.run = True
        self.bot = bot

    def stop(self) -> None:
        self.run = False

    async def handler(self):
        # Loop forever, checking the system time every second
        prev_dt = monotonic()
        while self.run:
            if monotonic() - prev_dt > 600:
                prev_dt = monotonic()
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

    bot_task = loop.create_task(ab.start_polling())
    time_task = loop.create_task(tester.handler())

    def signal_handler():
        print()
        tester.stop()
        ab.stop_bot()

    # Add a signal handler to catch the KeyboardInterrupt signal
    loop.add_signal_handler(signal.SIGINT, signal_handler)

    # Run the event loop until either task completes
    await asyncio.gather(bot_task, time_task)


if __name__ == "__main__":
    asyncio.run(main())
