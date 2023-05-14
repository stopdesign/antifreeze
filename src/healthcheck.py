from copy import deepcopy
from datetime import datetime

STATE: dict[str, str | None] = {}


def test(func):
    """
    Декоратор ловит AssertionError и складывает
    в глобальную переменную STATE.
    """
    STATE[func.__qualname__] = None

    def decorator(class_obj, *args, **kwargs):
        try:
            func(class_obj, *args, **kwargs)
        except AssertionError as e:
            STATE[func.__qualname__] = str(e)
        else:
            STATE[func.__qualname__] = "OK"

    return decorator


def changes(func):
    """
    Декоратор возвращает изменившиеся значения
    в формате {"проверка": "результат"}.
    """

    def decorator(class_obj, *args, **kwargs) -> dict:
        prev_state = deepcopy(STATE)
        func(class_obj, *args, **kwargs)
        changes = {}
        for key, value in STATE.items():
            prev_value = prev_state.get(key)
            if value != prev_value:
                # Переход из None в OK не считается
                if prev_value is None and value == "OK":
                    continue
                changes[key] = value
        return changes

    return decorator


class Gateway:
    @changes
    def check_ib_status(self, data) -> dict:  # type: ignore
        ibc_login = data.get("ibc_login")
        con = data.get("connections", {})
        dt = data.get("dt")

        self.auth_state(ibc_login)
        self.api_server(con.get("Interactive Brokers API Server"))
        self.status_delay(dt)

        md = con.get("Market Data Farm", {})
        hd = con.get("Historical Data Farm", {})

        self.market_data(md)
        self.historical_data(hd)

        recon = data.get("reconnecting", [])
        self.reconnecting(recon)

    @test
    def status_delay(self, value):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            delay = int((datetime.utcnow() - dt).total_seconds())
        except:
            raise AssertionError(f"Bad dt format {value}")
        assert delay < 10, f"Large delay: {delay} sec"

    @test
    def reconnecting(self, value):
        txt = ", ".join(value)
        assert len(txt) == 0, f"{txt}"

    @test
    def api_server(self, value):
        value = str(value).lower()
        assert value == "connected", f"state: {value}"

    @test
    def auth_state(self, value):
        value = str(value).lower()
        assert value == "logged_in", f"state: {value}"

    @test
    def market_data(self, data):
        data = {k: v for k, v in data.items() if v == "disconnected"}
        dead_farms = ", ".join(data.keys())
        assert len(dead_farms) == 0, f"failed: {dead_farms}"

    @test
    def historical_data(self, data):
        data = {k: v for k, v in data.items() if v == "disconnected"}
        dead_farms = ", ".join(data.keys())
        assert len(dead_farms) == 0, f"failed: {dead_farms}"
