"""Enterprise WeChat integration facade."""

from backend import legacy_runtime as legacy


def send(subject: str, message: str):
    return legacy.send_wecom_message(subject, message)
