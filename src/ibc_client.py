import json
import logging
import re
import socket

log = logging.getLogger("ibc_client")


class IbcClient:
    """
    Клиент для командного интерфейса IBC (telnet).
    Умеет отправлять команды и парсить статус подключений GW.
    """

    def __init__(self, host, port) -> None:
        self.host = host
        self.port = port
        self.socket_timeout = 1

    def _parse_on_off(self, value: str) -> dict:
        """
        Парсит вот такое говно:
        "ON: uscrypto, usfuture  OFFusfarm Inactiveushmds"
        """
        value = value.replace(" OFF", " OFF ")
        value = value.lower()
        value = value.replace(" inactive", " inactive ")
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

    def _parse_status(self, status: str) -> tuple[dict, dict, dict, list]:
        gateway, market, historical, retry = {}, {}, {}, []

        try:
            if "OK {" not in str(status):
                log.error(f"Bad status: {status}")
                s = {}
            else:
                s = json.loads(status.replace("OK {", "{"))
        except Exception as e:
            log.error("Error parsing json from INFO")
            log.exception(e)
            return gateway, market, historical, retry

        con = s.get("connections", {})

        gateway["IBC auth"] = s.get("ibc_login", "--")
        gateway["API Server"] = con.get("Interactive Brokers API Server", "--")
        gateway["API Clients"] = con.get("API Client", "--")

        market = self._parse_on_off(con.get("Market Data Farm", ""))
        historical = self._parse_on_off(con.get("Historical Data Farm", ""))

        retry = s.get("reconnecting", [])

        return gateway, market, historical, retry

    def get_status(self) -> tuple[dict, dict, dict, list]:
        """
        Статус IBC в четырех блоках:
        gateway, market, historical, retry
        """
        ibc_info = self.run_command("INFO")
        return self._parse_status(ibc_info)

    def run_command(self, command) -> str:
        """
        Подключается в сокет канала управления IBC,
        отправляет команду, читает ответ.
        """
        result = ""

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.socket_timeout)

        try:
            sock.connect((self.host, self.port))
            sock.sendall((f"{command}\n").encode())
            result = sock.recv(1024).decode("utf-8")
            sock.sendall(b"EXIT\n")
        except Exception as e:
            result = f"Error: {e}"
        finally:
            sock.close()

        return result
