#!/bin/python3

import time
import requests
import dateutil
from requests.structures import CaseInsensitiveDict
from threading import Thread,Timer
import pandas as pd
import os
from os import path

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
    #date = datetime.strptime(prod["Timestamp"], '%Y-%m-%dT%H::%M::%S.%f')
    #row.to_csv(outf, mode='a', header=not os.path.exists(outf))
    if path.exists(outf):
        with pd.ExcelWriter(outf, mode="a",if_sheet_exists="overlay") as writer:
            row.to_excel(writer,header=False)
    else:
        with pd.ExcelWriter(outf, mode="w") as writer:
            row.to_excel(writer)
    return True

def act(todo,saving):
    if todo:
        pass
    else:
        pass
    return True

def on(saving):
    return act(True,saving)

def off(saving):
    return act(False,saving)

def calculate(prod=0.0,cons=0.0,thresold=0):
    saving = max(0,prod-cons)
    print(prod,cons,saving)
    if saving > thresold:
        return (on,saving)
    else:
        return (off,saving)

def elaborate(devprod,devcons,avg,thresold,interval,outfile,server):
    while True:
      prod = (get_value(server,devprod,avg))
      cons = (get_value(server,devcons,avg))
      write_values(prod,cons,outfile)
      calculate(prod["PowerL1"],cons["PowerL1"],thresold=16.0)
      time.sleep(interval)

def main():
    t = Thread(target=elaborate, args=("SDM1.2","SDM1.1",False,16.0,5,"misure","http://192.168.1.127:8080"))
    t.start() 

if __name__ == "__main__":
    main()
