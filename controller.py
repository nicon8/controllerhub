#!/bin/python3

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread

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


def elaborate(config, controller, stop_event=None):
    stop_event = stop_event or Event()

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
            calculate(
                read_power(prod, "PowerL1"),
                read_power(cons, "PowerL1"),
                threshold=config.control_deadband_w,
                controller=controller,
            )
        except (requests.RequestException, ValueError, KeyError) as exc:
            logging.warning("ciclo saltato: %s", exc)

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
    stop_event = Event()
    thread = Thread(target=elaborate, args=(config, controller, stop_event))

    try:
        thread.start()
        thread.join()
    except KeyboardInterrupt:
        logging.info("arresto richiesto")
        stop_event.set()
        thread.join()
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
