import logging

from dbus_fast import BusType
from dbus_fast.aio.message_bus import MessageBus

log = logging.getLogger("systemd")


DBUS_PATH = "/org/freedesktop/systemd1"
DBUS_NAME = "org.freedesktop.systemd1"


class SystemdClient:
    def __init__(self) -> None:
        pass

    async def connect(self) -> bool:
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.introspection = await self.bus.introspect(DBUS_NAME, DBUS_PATH)
        obj = self.bus.get_proxy_object(DBUS_NAME, DBUS_PATH, self.introspection)
        self.manager = obj.get_interface(f"{DBUS_NAME}.Manager")
        return bool(self.manager)

    async def service_properties(self, service: str, properties: list) -> dict:
        """
        Свойства сервисов systemd.
        """
        unit = await self.manager.call_load_unit(service)  # type: ignore
        obj = self.bus.get_proxy_object(DBUS_NAME, unit, self.introspection)
        prop = obj.get_interface("org.freedesktop.DBus.Properties")
        res = {}
        for key in properties:
            state = await prop.call_get(f"{DBUS_NAME}.Unit", key)  # type: ignore
            res[key] = str(state.value)
        return res

    async def service_command(self, service: str, command: str) -> None:
        """
        Команды systemd через DBus
        """
        job_starter = getattr(self.manager, f"call_{command}_unit")
        job = await job_starter(service, "replace")  # type: ignore
        log.info(f"DBus command: {service}, {command}, job: {job}")
