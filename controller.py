#!/bin/python3

import csv
import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

import requests
from dateutil import parser

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


@dataclass(frozen=True)
class Config:
    production_device: str = "SDM1.2"
    consumption_device: str = "SDM1.1"
    use_average: bool = False
    control_deadband_w: float = 400.0
    interval_s: float = 5.0
    output_file: str = "/home/pi/shared/misure"
    server: str = "http://localhost:8000"
    gpio_pin: int = 12
    pwm_frequency_hz: int = 1000
    request_timeout_s: float = 3.0
    watts_per_duty_step: float = 200.0
    max_duty_step: float = 5.0
    notifications_enabled: bool = True
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = "controller-caldaia-castellano"
    ntfy_timeout_s: float = 3.0
    notification_cooldown_s: float = 600.0
    notify_after_failures: int = 3
    status_timezone: str = "Europe/Rome"
    status_start_hour: int = 7
    status_end_hour: int = 21


class Notifier:
    def __init__(self, enabled, server, topic, timeout_s, cooldown_s):
        self.enabled = enabled and bool(topic)
        self.server = server.rstrip("/")
        self.topic = topic
        self.timeout_s = timeout_s
        self.cooldown_s = cooldown_s
        self._last_sent = {}

    def send(self, key, title, message, priority=3, force=False):
        if not self.enabled:
            return False

        now = time.monotonic()
        last_sent = self._last_sent.get(key, 0.0)
        if not force and now - last_sent < self.cooldown_s:
            return False

        try:
            response = requests.post(
                f"{self.server}/{self.topic}",
                data=message.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": str(priority),
                    "Tags": "warning" if priority >= 4 else "information_source",
                },
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            self._last_sent[key] = now
            return True
        except requests.RequestException as exc:
            logging.warning("notifica ntfy non inviata: %s", exc)
            return False


class ValveController:
    def __init__(self, pin, pwm_frequency_hz, watts_per_duty_step, max_duty_step):
        self.status = 0.0
        self.watts_per_duty_step = watts_per_duty_step
        self.max_duty_step = max_duty_step
        self._pwm = None

        if GPIO is None:
            logging.warning("RPi.GPIO non disponibile: controllo PWM disabilitato")
            return

        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(pin, GPIO.OUT)
            self._pwm = GPIO.PWM(pin, pwm_frequency_hz)
            self._pwm.start(0)
        except Exception as exc:
            self._pwm = None
            logging.warning("PWM non inizializzato: %s", exc)

    def update(self, surplus_w, deadband_w):
        if surplus_w > deadband_w:
            delta = surplus_w / self.watts_per_duty_step
        elif surplus_w < -deadband_w:
            delta = surplus_w / self.watts_per_duty_step
        else:
            delta = 0.0

        delta = max(-self.max_duty_step, min(self.max_duty_step, delta))
        self.status = max(0.0, min(100.0, self.status + delta))

        if self._pwm is not None:
            self._pwm.ChangeDutyCycle(self.status)

        logging.info(
            "surplus=%.1fW delta=%.2f duty=%.2f%%",
            surplus_w,
            delta,
            self.status,
        )
        return self.status

    def stop(self):
        if self._pwm is not None:
            self._pwm.ChangeDutyCycle(0)
            self._pwm.stop()
        if GPIO is not None:
            try:
                GPIO.cleanup()
            except Exception as exc:
                logging.warning("cleanup GPIO non completato: %s", exc)


def get_value(server, device="SDM1.1", avg=True, timeout=3.0):
    endpoint = "avg" if avg else "last"
    url = f"{server}/api/{endpoint}/{device}"
    response = requests.get(
        url,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def read_power(value, field):
    try:
        return float(value[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"valore sensore non valido per {field}: {value}") from exc


def output_path(outfile, timestamp):
    base = Path(outfile)
    suffix = f"_{timestamp.year}-{timestamp.month}-{timestamp.day}.csv"
    return base.with_name(base.name + suffix)


def write_values(prod, cons, outfile):
    timestamp = parser.parse(prod["Timestamp"])
    destination = output_path(outfile, timestamp)
    destination.parent.mkdir(parents=True, exist_ok=True)
    new_file = not destination.exists()

    row = {
        "Time": timestamp.strftime("%H:%M:%S"),
        "P Volt (V)": prod["VoltageL1"],
        "P Curr (A)": prod["CurrentL1"],
        "P Power (W)": prod["PowerL1"],
        "C Volt (V)": cons["VoltageL1"],
        "C Curr (A)": cons["CurrentL1"],
        "C Power (W)": cons["PowerL1"],
    }

    with destination.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=row.keys())
        if new_file:
            writer.writeheader()
        writer.writerow(row)

    return True


def calculate(prod=0.0, cons=0.0, threshold=0.0, controller=None):
    surplus = float(prod) - float(cons)
    if controller is None:
        return surplus
    return controller.update(surplus, threshold)


def notify_hourly_status(config, notifier, controller, prod_w, cons_w, last_status_hour):
    now = datetime.now(ZoneInfo(config.status_timezone))
    if not config.status_start_hour <= now.hour <= config.status_end_hour:
        return last_status_hour

    status_hour = now.strftime("%Y-%m-%d %H")
    if status_hour == last_status_hour:
        return last_status_hour

    surplus_w = prod_w - cons_w
    notifier.send(
        f"hourly-status-{status_hour}",
        "Controller caldaia: stato orario",
        f"Valvola aperta al {controller.status:.0f}%\n"
        f"Produzione: {prod_w:.0f} W\n"
        f"Consumo: {cons_w:.0f} W\n"
        f"Surplus: {surplus_w:.0f} W\n"
        f"Ora Roma: {now.strftime('%H:%M')}",
        priority=2,
        force=True,
    )
    return status_hour


def elaborate(config, controller, notifier, stop_event=None):
    stop_event = stop_event or Event()
    consecutive_failures = 0
    was_failing = False
    last_status_hour = None

    notifier.send(
        "startup",
        "Controller caldaia avviato",
        f"{socket.gethostname()} sta monitorando produzione e consumo.",
        priority=2,
        force=True,
    )

    while not stop_event.is_set():
        try:
            prod = get_value(
                config.server,
                config.production_device,
                config.use_average,
                config.request_timeout_s,
            )
            cons = get_value(
                config.server,
                config.consumption_device,
                config.use_average,
                config.request_timeout_s,
            )
            write_values(prod, cons, config.output_file)
            prod_w = read_power(prod, "PowerL1")
            cons_w = read_power(cons, "PowerL1")
            calculate(
                prod_w,
                cons_w,
                threshold=config.control_deadband_w,
                controller=controller,
            )

            if was_failing:
                notifier.send(
                    "sensor-recovered",
                    "Controller caldaia: sensori recuperati",
                    "Le letture dei sensori sono tornate disponibili.",
                    priority=3,
                    force=True,
                )
            consecutive_failures = 0
            was_failing = False
            last_status_hour = notify_hourly_status(
                config,
                notifier,
                controller,
                prod_w,
                cons_w,
                last_status_hour,
            )
        except (requests.RequestException, ValueError, KeyError) as exc:
            consecutive_failures += 1
            was_failing = True
            logging.warning("ciclo saltato: %s", exc)
            if consecutive_failures >= config.notify_after_failures:
                notifier.send(
                    "sensor-error",
                    "Controller caldaia: sensori non disponibili",
                    f"{consecutive_failures} cicli consecutivi falliti. "
                    f"Ultimo errore: {exc}",
                    priority=4,
                )

        stop_event.wait(config.interval_s)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = Config()
    controller = ValveController(
        config.gpio_pin,
        config.pwm_frequency_hz,
        config.watts_per_duty_step,
        config.max_duty_step,
    )
    notifier = Notifier(
        config.notifications_enabled,
        config.ntfy_server,
        config.ntfy_topic,
        config.ntfy_timeout_s,
        config.notification_cooldown_s,
    )
    stop_event = Event()
    thread = Thread(target=elaborate, args=(config, controller, notifier, stop_event))

    try:
        thread.start()
        thread.join()
    except KeyboardInterrupt:
        logging.info("arresto richiesto")
        notifier.send(
            "shutdown",
            "Controller caldaia arrestato",
            f"{socket.gethostname()} ha ricevuto un arresto manuale.",
            priority=2,
            force=True,
        )
        stop_event.set()
        thread.join()
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
