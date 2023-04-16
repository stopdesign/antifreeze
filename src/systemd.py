import logging

from dbus_fast import BusType
from dbus_fast.aio.message_bus import MessageBus

log = logging.getLogger("systemd")


DBUS_PATH = "/org/freedesktop/systemd1"
DBUS_NAME = "org.freedesktop.systemd1"


async def service_properties(service_name: str, properties: list) -> dict:
    """
    Свойства сервисов systemd.
    """
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    introspection = await bus.introspect(DBUS_NAME, DBUS_PATH)
    obj = bus.get_proxy_object(DBUS_NAME, DBUS_PATH, introspection)
    manager = obj.get_interface(f"{DBUS_NAME}.Manager")
    unit = await manager.call_load_unit(service_name)  # type: ignore
    obj = bus.get_proxy_object(DBUS_NAME, unit, introspection)
    prop = obj.get_interface("org.freedesktop.DBus.Properties")
    res = {}
    for key in properties:
        state = await prop.call_get(f"{DBUS_NAME}.Unit", key)  # type: ignore
        res[key] = str(state.value)
    return res


async def service_command(service_name: str, command: str) -> None:
    """
    Команды systemd через DBus
    """
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    introspection = await bus.introspect(DBUS_NAME, DBUS_PATH)
    obj = bus.get_proxy_object(DBUS_NAME, DBUS_PATH, introspection)
    manager = obj.get_interface(f"{DBUS_NAME}.Manager")
    job_starter = getattr(manager, f"call_{command}_unit")
    job = await job_starter(service_name, "replace")  # type: ignore
    log.info(f"DBus command: {service_name}, {command}, job: {job}")
