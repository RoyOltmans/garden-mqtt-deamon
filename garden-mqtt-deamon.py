#!/usr/bin/env python
# -*- encoding: utf-8 -*-

# ---------------------------------------------------------------------------------------
# Name:        garden_mqtt_servive service
# Purpose:     listening to MQTT posts and sending a request to a Aqualin celenoid valve
#              determening batteru status of every aqualin unit connected
#              determining status of mi flora sensors
#
# Author:      roy.oltmans
#
# Created:     18-11-2018
# Altered:     28-04-2020
# Copyright:   (c) roy.oltmans 2018
# Licence:     <your licence>
# ---------------------------------------------------------------------------------------
from bluepy import btle
from Queue import Queue
from threading import Thread
import paho.mqtt.client as mqtt
import main_utils, os, sys, syslog, time, schedule, binascii, miplant_lib

tools = main_utils.tools()

trace = tools.fetchConfig().get('GENERAL', 'errortrace')
bleHandle = tools.fetchConfig().get('AQUALIN', 'bleHandle')
valuebase = tools.fetchConfig().get('AQUALIN', 'valueBase')
waittime = tools.fetchConfig().get('AQUALIN', 'waittime')
location = tools.fetchConfig().get('AQUALIN', 'location')
devicemaclist = tools.fetchConfig().get('AQUALIN', 'macaddresslist')
stdwatertimer = tools.fetchConfig().get('AQUALIN', 'stdwatertimer')
intvalveinterval = int(tools.fetchConfig().get('AQUALIN','valve_interval'))
strmqtthost = tools.fetchConfig().get('MQTT', 'Host')
intmqttport = int(tools.fetchConfig().get('MQTT', 'Port'))
mqttbasepath_aqualin = tools.fetchConfig().get('MQTT', 'mqttbasepath_aqualin')
mqttbasepath_miflora = tools.fetchConfig().get('MQTT', 'mqttbasepath_miflora')
mqttbasepath = tools.fetchConfig().get('MQTT', 'mqttbasepath')
intmiflorainterval = int(tools.fetchConfig().get('MIFLORA','miflora_interval'))

# Set up some global variables
devicemaclist = devicemaclist.split(',')
num_fetch_threads = 2
mqtt_Queue = Queue()
battery_Queue = Queue()
deviceblelock = False
devstatus = []


def on_connect(client, userdata, flags, rc):  # The callback for when the client receives a response from the server.
    if trace:
        print "MQTT: Connected NOK with result code " + str(rc)
        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
    syslog.syslog("MQTT: Connected OK with result code " + str(rc))
    client.subscribe('home/#')


def on_message(client, userdata, msg):  # When a message is received on the MQTT bus
    if 'aqualin' in msg.topic.split("/"):
        state = msg.payload.split(",")[0]
        if 'set' in msg.topic.split("/"):  # check if the req is applicable
            if state == 'on':  # check kind of request
                timervalue = msg.payload.split(",")[1]
                if timervalue <= 0:
                    timervalue = stdwatertimer
                devstatus.append(msg.topic.split("/")[2])
            elif state == 'off':
                timervalue = 00
		devstatus.remove(msg.topic.split("/")[2])
        if 'status' in msg.topic.split("/"):  # check if status req is applicable
            if msg.topic.split("/")[2] in devstatus:
                client.publish(mqttbasepath_aqualin + msg.topic.split("/")[2] + '/power','on')
            else:
                client.publish(mqttbasepath_aqualin + msg.topic.split("/")[2] + '/power','off')
            if state == 'check':
                runbatterycheck()
	mqtt_Queue.put((state, timervalue, msg.topic.split("/")[2]))
        mqtt_Queue.qsize()
        state = ''
    elif 'miflora':
        if 'status' in msg.topic.split("/"):
            if str(msg.payload) == 'check':
                runmiflora()


def connectbledevice(devicemac):
    global deviceblelock
    retry = 0
    frstblockloop = True
    conn = btle.Peripheral()
    while True:
        if not deviceblelock:
            deviceblelock = True
            try:
                if trace:
                    print "Connected to Aqualin " + str(devicemac)
                conn.connect(str(devicemac))
                frstblockloop = True
                return conn
            except btle.BTLEException as ex:
                if trace:
                    print "Unable to connect to:" + str(devicemac)
                connectbledevice(str(devicemac))
        else:
            if frstblockloop:
                print "Device Blocked Waiting"
                frstblockloop = False
            else:
                print "."
        if trace:
            print "BLE locked"
            print "Retry number: " + str(retry)
        if retry == 4:
            conn.disconnect()
            retry = 0
            deviceblelock = False
            return False
        retry += 1
        time.sleep(5)

def runworkerbledevicestate():
    while True:
        queuevalues = mqtt_Queue.get()
        if trace:
            print queuevalues
            setblerequest(queuevalues[0], queuevalues[2], bleHandle, getblevalue(queuevalues[0], queuevalues[1]))
            # Initialize ble req
            if trace:
                print "BLE instruction send..."

def runvalvecheck():
    global deviceblelock
    for i, devicemac in enumerate(devicemaclist):
        if deviceblelock == False:
            device = connectbledevice(str(devicemac))
            intsolenoidstate = getsolenoidvalve(devicemac)
            if intsolenoidstate <= 0:
                strvalvestate = 'off'
            else:
                strvalvestate = 'on'
            client.publish(mqttbasepath + str(devicemac) + '/valvestate', strvalvestate)  # publish
            if trace:
                print "Valve state is " + strvalvestate + " for device " + str(devicemac)
            time.sleep(int(waittime))
            device.disconnect()
            deviceblelock = False
        else:
            time.sleep(int(waittime))

def runbatterycheck():
    global deviceblelock
    for i, devicemac in enumerate(devicemaclist):
        while True:
            if not deviceblelock:
                device = connectbledevice(str(devicemac))
                strbatstatus = str(binascii.b2a_hex(device.readCharacteristic(0x0081)).decode('utf-8'))
                client.publish(mqttbasepath_aqualin + str(devicemac) + '/battery', strbatstatus)  # publish
                if trace:
                    print "Battery status " + str(strbatstatus) + "% of device " + str(devicemac)
                time.sleep(int(waittime))
                device.disconnect()
                deviceblelock = False
                break
            time.sleep(int(waittime))

def setblerequest(status, devicemac, bleHandle, bleValue):
    global deviceblelock
    if trace:
        print "Connecting " + devicemac + "..."
    while True:
        if not deviceblelock:
            device = connectbledevice(str(devicemac))
            device.writeCharacteristic(0x0073, bleValue, withResponse=True)
            if trace:
                print "Value parsed " + str(bleValue) + " seconds..."
            time.sleep(int(waittime))
            if trace:
                print "Wait " + str(waittime) + " seconds..."
            device.disconnect()
            client.publish(mqttbasepath_aqualin + str(devicemac) + '/command/executed', str(status) + ',' + str(bleValue.encode("hex")))  # publish
            deviceblelock = False
            break
        time.sleep(int(waittime))

def getsolenoidvalve(devicemac):
    global deviceblelock
    if trace:
        print "Connecting " + devicemac + "..."    
    if deviceblelock == False:
        device = connectbledevice(str(devicemac))
        if device:
            intvalvestate = int(str(binascii.b2a_hex(device.readCharacteristic(0x0073)).decode('utf-8'))[6:10],16)
            time.sleep(int(waittime))
            device.disconnect()
            deviceblelock = False
        else:
            return
        return intvalvestate
    else:
        time.sleep(int(waittime))

# transform and prepair the Value response on or off and time the valve will be active
def getblevalue(status, timer):
    valuerequest = ''  # type: str
    valuestatus = '00'
    valuecalltimer = '0000'
    valuecalltimer = hex(int(timer)).split('x')[-1]  # decimal to hex conversion for the of time
    valuecalltimer = valuecalltimer.rjust(4, '0')  # if the value is shorter than 4 digits fill them up with zeros
    if status == 'on':
        valuestatus = '01'
    elif status == 'off':
        valuestatus = '00'
    valuerequest += valuebase
    valuerequest += valuestatus
    valuerequest += valuecalltimer
    if trace:
        print "BLE instruction: "
        print valuerequest
    return valuerequest.decode("hex")  # payload preperation to hex for writing to valve


def runmiflora():
    global deviceblelock
    while True:
        if not deviceblelock:
            deviceblelock = True
            for plant in miplant_lib.MiPlant.discover(interface_index=0, timeout=5):
                if trace:
                    print('--------------------------')
                    print('Address: %s' % plant.address)
                    print('Battery level: %i%%' % plant.battery)
                    print('Firmware: %s' % plant.firmware)
                    print('Temperature: %.01f °C' % plant.temperature)
                    print('Light: %.0f lx' % plant.light)
                    print('Moisture: %.0f%%' % plant.moisture)
                    print('Conductivity: %.0f µS/cm' % plant.conductivity)
                # publish Battery level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/battery', plant.battery)
                # publish Firmware level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Firmware', plant.firmware)
                # publish Temperature level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Temperature', plant.temperature)
                # publish Light level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Light', plant.light)
                # publish Moisture level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Moisture', plant.moisture)
                # publish Conductivity level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Conductivity', plant.conductivity)
            deviceblelock = False
            break
        time.sleep(int(waittime))

def runworkerscheduler():
    while True:
        schedule.run_pending()
        time.sleep(int(waittime))


if __name__ == '__main__':
    # First thread creating Queue for state calls
    t1 = Thread(target=runworkerbledevicestate )  # Start worker thread 1
    t1.setDaemon(True)
    t1.start()

    # Setting itteration check miflora sensors status every 5 minutes
    schedule.every(intmiflorainterval).seconds.do(runmiflora)  # for checking thread fullfillment

    # Setting itteration check battery status every week on sunday and reporting on MQTT
	#Disabled battery check due to unavalibi;ity of celenoid valves
    #schedule.every(86400).seconds.do(runbatterycheck)  # for checking thread fullfillment
    #schedule.every().sunday.at("23:55").do(runbatterycheck)

    # Setting itteration check valve status every 6min and reporting on MQTT
    schedule.every(intvalveinterval).seconds.do(runvalvecheck)  # for checking thread fullfillment

    # Second Thread Battery status checks all devices
    t2 = Thread(target=runworkerscheduler)  # Start worker thread
    t2.setDaemon(True)
    t2.start()

    # MQTT Startup
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(strmqtthost, intmqttport, 60)
    client.loop_forever()
