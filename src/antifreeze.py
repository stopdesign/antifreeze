import asyncio
import logging
import signal
from datetime import datetime, timedelta
from functools import wraps
from time import monotonic, sleep
from zoneinfo import ZoneInfo

import coloredlogs
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, Filter, Text
from aiogram.types import KeyboardButton, Message
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.utils.markdown import hpre
from ib_sync import IBSync, IBThread

from ibc_client import IbcClient
from settings import app_config
from systemd import SystemdClient

# Enable logging
fmt = "%(asctime).19s • %(levelname).1s • %(name)s • %(message)s"
coloredlogs.install("INFO", fmt=fmt)
log = logging.getLogger("antifreeze")

logging.getLogger("ib_api.client").setLevel(logging.ERROR)
logging.getLogger("ib_api.ib_sync").setLevel(logging.ERROR)


"""
BotFather commands:
re_data - Reconnect data
re_acc - Reconnect account
ibc_restart - Restart gateway with IBC
gw_start - Start gateway sevice
gw_stop - Stop gateway sevice
"""

TIME_ZONE = ZoneInfo("America/Los_Angeles")

TG_TOKEN = app_config.telegram.token

GATEWAY_HOST = app_config.gateway.host
GATEWAY_PORT = app_config.gateway.port
CLIENT_ID = app_config.gateway.client_id

ADMINS = app_config.telegram.admins

TO_CHECK = app_config.systemd.check
TO_CONTROL = app_config.systemd.control

IBC_HOST = app_config.ibc.host
IBC_PORT = app_config.ibc.port

ibc_client = IbcClient(IBC_HOST, IBC_PORT)
systemd_client = SystemdClient()


def readable_timedelta(duration: timedelta):
    data = {}
    data['days'], remaining = divmod(duration.total_seconds(), 86_400)
    data['hours'], remaining = divmod(remaining, 3_600)
    data['min'], data['sec'] = divmod(remaining, 60)

    if duration >= timedelta(hours=1):
        del data['sec']

    parts = [f"{round(v)} {k}" for k, v in data.items() if v > 0][:2]
    if parts:
        return " ".join(parts)
    else:
        return "below 1 sec"


async def ibgw_short_status():
    ib = IBSync()

    txt = ""
    try:
        ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=CLIENT_ID)
        IBThread(ib).start()

        dt = monotonic()
        while not sleep(0.01) and monotonic() - dt < 3:
            if ib.nextValidOrderId > 0:
                break

        if not ib.nextValidOrderId > 0:
            return "Not connected"

        fields, positions = ib.get_account_info()
        nv = float(fields.get("NetLiquidation", "nan"))

        txt = f"Net Value:   {nv:,.0f} USD".replace(",", " ")

    except Exception as e:
        txt = f"ERROR: {e}"

    finally:
        ib.disconnect()

    return txt


async def ibgw_account_info():
    """
    Запрос информации об аккаунте из IBGW.
    """
    ib = IBSync()

    txt = ""
    try:
        ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=CLIENT_ID)
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
    div = "==========================\n"

    # Статусы сервисов systemd
    for s in TO_CHECK:
        txt += f"\n{s}\n" + div
        try:
            service = s if ".service" in s else f"{s}.service"
            txt += await check_service(service)
        except:
            txt += "Status             unknown\n"

    # log.info(f"Services {txt}")

    # запросить INFO через телнет IBC
    gateway, market, hist, retry = ibc_client.get_status()

    # log.info(f"Services {ibc_info}")

    txt += "\nGateway\n" + div
    for key, val in gateway.items():
        txt += f"{key:<12}{val:>14}\n"

    txt += "\nMarket data\n" + div
    for key, val in market.items():
        txt += f"{key:<12}{val:>14}\n"

    txt += "\nHistorical data\n" + div
    for key, val in hist.items():
        txt += f"{key:<12}{val:>14}\n"

    return txt.strip().replace(" ", " ") + "\n⠀", retry


async def check_service(service: str) -> str:
    res = ""
    props = ["ActiveState", "SubState", "StateChangeTimestamp"]

    values = await systemd_client.service_properties(service, props)
    status = "{ActiveState}, {SubState}".format(**values)
    res += f"State {status:>20}\n"

    ts = int(values["StateChangeTimestamp"])
    dt = datetime.utcfromtimestamp(ts // 1000000)
    delta = readable_timedelta(datetime.utcnow() - dt)
    res += f"Time {delta:>21}\n"

    return res


async def systemd_command(command: str) -> None:
    if command not in ["start", "stop", "restart"]:
        raise ValueError(f"Unknown command {command}")
    for s in TO_CONTROL:
        service = s if ".service" in s else f"{s}.service"
        await systemd_client.service_command(service, command)


HANDLERS = []


def restricted(filter: Filter, allowed_users: list):
    """
    Декоратор для регистрации обработчиков сообщений
    с ограничением доступа по белому списку юзеров.
    """

    def decorator(function):
        @wraps(function)
        async def wrapper(obj, message):
            chat = message.chat
            if chat.type == "private" and chat.id in allowed_users:
                return await function(obj, message)
            else:
                log.error(f"Unknown user: {message}")
                return

        HANDLERS.append((wrapper, filter))
        return wrapper

    return decorator


class AntifreezeBot:
    bot: Bot
    dp: Dispatcher

    def __init__(self, loop) -> None:
        self.subscribers = list(ADMINS) or []
        self.loop = loop

        self.router = Router()
        self.dp = Dispatcher()
        self.dp.include_router(self.router)
        self.bot = Bot(token=TG_TOKEN, parse_mode="HTML")

        builder = ReplyKeyboardBuilder()
        builder.row(
            KeyboardButton(text="🚀 Service⠀"),
            KeyboardButton(text="💰 Account⠀"),
        )
        self.markup = builder.as_markup(
            is_persistent=True,
            resize_keyboard=True,
        )

        self.dp.startup.register(self.on_startup)

        # Регистрация обработчиков
        for callback, filter in HANDLERS:
            func = getattr(self, callback.__name__)
            self.router.message.register(func, filter)

    async def typing(self, message: Message):
        await self.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    async def start_polling(self):
        log.info("Start bot")

        # Не обрабатывать старые сообщения
        # https://github.com/aiogram/aiogram/issues/418
        await self.bot.delete_webhook(drop_pending_updates=True)

        await self.dp.start_polling(
            self.bot,
            skip_updates=True,
            handle_signals=False,
        )

    async def _stop_bot(self):
        await self.dp.stop_polling()

    def stop_bot(self):
        self.loop.create_task(self._stop_bot())

    async def errors_handler(self, update, exception):
        log.error(f"Bot exception: {exception} | {update}")

    async def on_startup(self):
        """
        Сообщение всем подписчикам при старте бота.
        """
        welcome_msg = "Antifreeze!"
        log.info(f"Send start msg to {self.subscribers}")
        for user_id in self.subscribers:
            try:
                await self.bot.send_message(
                    user_id,
                    welcome_msg,
                    reply_markup=self.markup,
                )
            except Exception as e:
                log.error(e)

    ##################################################
    # Подключение бота и подписка на спам

    @restricted(Command("start"), ADMINS)
    async def tg_start(self, message: Message):
        chat = message.chat
        if chat.type == "private" and chat.id not in self.subscribers:
            self.subscribers.append(chat.id)
            log.info(f"Start spamming user {chat.username}")
            await message.answer("Ok", reply_markup=self.markup)
        else:
            await message.answer("Nothing", reply_markup=self.markup)

    @restricted(Command("stop"), ADMINS)
    async def tg_stop(self, message: Message):
        if message.chat.id in self.subscribers:
            self.subscribers.remove(message.chat.id)
            log.info(f"Stop spamming user {message.chat.username}")
            await message.answer("Stopped")
        else:
            await message.answer("Subscriber not found")

    ##################################################
    ## Команды IBC

    @restricted(Command("re_data"), ADMINS)
    async def tg_reconnect_data(self, message: Message):
        await self.typing(message)
        res = ibc_client.run_command("RECONNECTDATA")
        await message.answer(f"Result: {res}")

    @restricted(Command("re_acc"), ADMINS)
    async def tg_reconnect_account(self, message: Message):
        await self.typing(message)
        res = ibc_client.run_command("RECONNECTACCOUNT")
        await message.answer(f"Result: {res}")

    @restricted(Command("ibc_restart"), ADMINS)
    async def tg_ibc_restart(self, message: Message):
        await self.typing(message)
        res = ibc_client.run_command("RESTART")
        await message.answer(f"Result: {res}")

    ##################################################
    ## Команды Systemd

    @restricted(Command("gw_start"), ADMINS)
    async def tg_gw_start(self, message: Message):
        await self.typing(message)
        await systemd_command("start")
        await message.answer(f"Ok")

    @restricted(Command("gw_stop"), ADMINS)
    async def tg_gw_stop(self, message: Message):
        await self.typing(message)
        await systemd_command("stop")
        await message.answer(f"Ok")

    @restricted(Command("gw_restart"), ADMINS)
    async def tg_gw_restart(self, message: Message):
        await self.typing(message)
        await systemd_command("restart")
        await message.answer(f"Ok")

    ##################################################
    ## Запрос информации

    @restricted(Text("💰 Account⠀"), ADMINS)
    async def tg_account_info(self, message: Message):
        log.info(f"Account info requested")
        await self.typing(message)
        res = await ibgw_account_info()
        await message.answer(f"{hpre(res)}")

    @restricted(Text("🚀 Service⠀"), ADMINS)
    async def tg_service_info(self, message: Message):
        log.info(f"Service info requested")
        await self.typing(message)

        res, retries = await service_status()
        await message.answer(f"{hpre(res)}")

        # Сообщения вида "Attempt 6: connection error..."
        for retry in retries:
            await message.answer(retry)

    ##################################################

    async def periodic_status(self, msg: str):
        for user_id in self.subscribers:
            try:
                await self.bot.send_message(user_id, msg, disable_notification=True)
            except Exception as e:
                log.error(e)

    async def error_alert(self, msg: str):
        for user_id in ADMINS:
            try:
                await self.bot.send_message(user_id, msg)
            except Exception as e:
                log.error(e)


class Tester:
    def __init__(self, bot) -> None:
        self.run = True
        self.bot = bot
        # self.schedule = []

    def stop(self) -> None:
        self.run = False

    async def status(self):
        """
        Отправка статуса каждый час.
        """
        prev_dt = 0
        while self.run:
            dt = datetime.now().astimezone(TIME_ZONE)
            if monotonic() - prev_dt > 300 and dt.minute == 5:
                prev_dt = monotonic()
                res = await ibgw_short_status()
                if "ERROR" in str(res).upper():
                    await self.bot.error_alert(res)
                else:
                    await self.bot.periodic_status(res + f" {dt}")
            await asyncio.sleep(1)
        log.error("Tester status out")

    async def healthcheck(self):
        """
        Проверка статуса, отправка ошибок.
        """
        prev_dt = 0
        status_ok = True
        while self.run:
            dt = datetime.now().astimezone(TIME_ZONE)

            if dt.second % 30 == 0:
                await asyncio.sleep(1)
                txt = ""

                ib_status = await ibgw_short_status()
                gw, retry = await service_status()

                # Не получен баланс
                if "ERROR" in ib_status.upper():
                    txt += hpre(f"IB ERROR:\n\n{ib_status}\n\n")

                # IBC не вернул статус LOGGED_IN
                if "LOGGED_IN" not in str(gw).upper():
                    txt += hpre(f"Auth ERROR:\n\n{gw}\n\n")

                # Один из сервисов не запущен
                if "failed" in gw or "dead" in gw or "unknown" in gw:
                    txt += hpre(f"Service ERROR:\n\n{gw}\n\n")

                # Происходит перелогин в IB
                if retry:
                    txt += hpre(f"Reconnecting:\n\n{retry}\n\n")

                if txt:
                    # Отправлять алерты не чаще раза в час
                    status_ok = False
                    if monotonic() - prev_dt >= 3600:
                        await self.bot.error_alert(txt.strip())
                        prev_dt = monotonic()
                else:
                    # Таймер и флаг ошибки сбрасываются,
                    # когда приходит нормальный статус.
                    if status_ok is False:
                        status_ok = True
                        await self.bot.periodic_status("Status OK")
                    prev_dt = 0

            await asyncio.sleep(0.2)
        log.error("Tester healthcheck out")


async def main():
    # Set up the asyncio event loop and tasks
    loop = asyncio.get_running_loop()

    try:
        await systemd_client.connect()
    except Exception as e:
        log.error(f"Can't connect to DBus: {e}")

    ab = AntifreezeBot(loop)
    tester = Tester(ab)

    bot_task = loop.create_task(ab.start_polling())
    st_task = loop.create_task(tester.status())
    hc_task = loop.create_task(tester.healthcheck())

    def signal_handler():
        print()
        tester.stop()
        ab.stop_bot()

    # Add a signal handler to catch the KeyboardInterrupt signal
    loop.add_signal_handler(signal.SIGINT, signal_handler)

    # Run the event loop until either task completes
    await asyncio.gather(bot_task, st_task, hc_task)


if __name__ == "__main__":
    asyncio.run(main())
