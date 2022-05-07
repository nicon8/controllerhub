#!/bin/python3

import time
import requests
import dateutil
from requests.structures import CaseInsensitiveDict
from threading import Thread,Timer
import pandas as pd
import os
from os import path
import RPi.GPIO as GPIO

data = pd.DataFrame()
status = 0
pin = 12

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(pin,GPIO.OUT)
pi_pwm = GPIO.PWM(pin,1000)
pi_pwm.start(0)

def get_value(server,device="SDM1.1",avg=True):
  if avg:
    url = server+"/api/avg/"+device
  else:
    url = server+"/api/last/"+device
  headers = CaseInsensitiveDict()
  headers["Accept"] = "application/json"
  resp = requests.get(url, headers=headers)
  return(resp.json())


def write_values(prod,cons,outfile):
    global data
    record = {}
    date = dateutil.parser.parse(prod["Timestamp"])
    outf = (outfile+"_"+str(date.year)+"-"+str(date.month)+"-"+str(date.day)+".xlsx")
    record["Time"] = [date.strftime("%H:%M:%S")]
    record["P Volt (V)"] = [prod["VoltageL1"]]
    record["P Curr (A)"] = [prod["CurrentL1"]]
    record["P Power (W)"] = [prod["PowerL1"]]
    record["C Volt (V)"] = [cons["VoltageL1"]]
    record["C Curr (A)"] = [cons["CurrentL1"]]
    record["C Power (W)"] = [cons["PowerL1"]]
    row = pd.DataFrame(record)
    data = pd.concat([data,row])
    data.to_excel(outf)
    return True

def act(todo,saving):
    global status
    global pi_pwm
    if todo:
        print("Poweron")
        status = min(100, status + (saving / 200) )
    else:
        print("Reduction")
        status = max(0,status + (saving / 200))

    print(status)
    pi_pwm.ChangeDutyCycle(status) 
    return True

def on(saving):
    return act(True,saving)

def off(saving):
    return act(False,saving)

def calculate(prod=0.0,cons=0.0,thresold=0):
    global status
    saving = prod-cons
    print(prod,cons,saving,status)
    if saving > thresold:
        return on(saving)
    else:
        return off(saving)

def elaborate(devprod,devcons,avg,thresold,interval,outfile,server):
    while True:
      prod = (get_value(server,devprod,avg))
      cons = (get_value(server,devcons,avg))
      write_values(prod,cons,outfile)
      calculate(prod["PowerL1"],cons["PowerL1"],thresold=400.0)
      time.sleep(interval)

def main():
    t = Thread(target=elaborate, args=("SDM1.2","SDM1.1",False,16.0,5,"/home/pi/shared/misure","http://192.168.1.127:8080"))
    t.start() 

if __name__ == "__main__":
    main()
