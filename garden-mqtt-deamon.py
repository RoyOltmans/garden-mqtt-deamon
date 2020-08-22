#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

# ---------------------------------------------------------------------------------------
# Name:        aqualin_mqtt_servive service
# Purpose:     sending mqtt messages with status of Aqualin valves (on/off/timer)
#              sending mqtt messages with status of MiFlora sensors 
#              receiving mqtt messages to set the Aqualin valves (on/off/timer)
#
# Author:      ernst klamer, originally developed by roy.oltmans
#
# Created:     22-08-2020
# Copyright:   (c) 2020 Ernst Klamer, roy.oltmans
# Licence:     MIT License
# ---------------------------------------------------------------------------------------

from bluepy import btle
from queue import Queue
from threading import Thread
import paho.mqtt.client as mqtt
import main_utils, os, sys, syslog, time, schedule, binascii, miplant_lib

tools = main_utils.tools()

trace = tools.fetchConfig().get('GENERAL', 'errortrace')
bleHandle = tools.fetchConfig().get('AQUALIN', 'bleHandle')
valuebase = tools.fetchConfig().get('AQUALIN', 'valueBase')
waittime = tools.fetchConfig().get('AQUALIN', 'waittime')
devicemaclist = tools.fetchConfig().get('AQUALIN', 'macaddresslist')
stdwatertimer = tools.fetchConfig().get('AQUALIN', 'stdwatertimer')
intvalveintervalon = int(tools.fetchConfig().get('AQUALIN','valve_interval_on'))
intvalveintervaloff = int(tools.fetchConfig().get('AQUALIN','valve_interval_off'))
strmqtthost = tools.fetchConfig().get('MQTT', 'Host')
intmqttport = int(tools.fetchConfig().get('MQTT', 'Port'))
strmqttusername = tools.fetchConfig().get('MQTT', 'Username')
strmqttpassword = tools.fetchConfig().get('MQTT', 'Password')
mqttbasepath_aqualin = tools.fetchConfig().get('MQTT', 'mqttbasepath_aqualin')
mqttbasepath_miflora = tools.fetchConfig().get('MQTT', 'mqttbasepath_miflora')
intmiflorainterval = int(tools.fetchConfig().get('MIFLORA','miflora_interval'))

# Set up some global variables
devicemaclist = devicemaclist.split(',')
num_fetch_threads = 2
mqtt_Queue = Queue()
battery_Queue = Queue()
deviceblelock = False


def on_connect(client, userdata, flags, rc):  # The callback for when the client receives a response from the server.
    syslog.syslog("MQTT: Connected with result code " + str(rc))
    if trace:
        print("MQTT: Connected with result code " + str(rc))
        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
    client.subscribe(mqttbasepath_aqualin+'#')


def on_message(client, userdata, msg):  # When a message is received on the MQTT bus
    if 'aqualin' in msg.topic.split("/"):
        if 'switch' in msg.topic.split("/"):  # check kind of request
            timervalue = int(msg.payload)       
            if timervalue <= 0:
                status = 'off'
                timervalue = 00
                print("Message received to set valve to off ({:d} minutes)".format(timervalue))
            elif timervalue > 0:
                status = 'on'
                print("Message received to set valve to on ({:d} minutes)".format(timervalue))
            mqtt_Queue.put((status,timervalue,msg.topic.split("/")[2]))
            mqtt_Queue.qsize()
    elif 'miflora' in msg.topic.split("/"):
        if 'status' in msg.topic.split("/"):
            if str(msg.payload) == 'check':
                runmiflora()


def runworkerbledevice():
    publishvalvestate()
    while True:
        queuevalues = mqtt_Queue.get()
        changestate = queuevalues[0]
        changetimer = queuevalues[1]
        devicemac = queuevalues[2]
        try:
            # Initialize ble req
            setblerequest(changestate, devicemac, bleHandle, getblecode(changestate, changetimer))
            if trace:
                print("BLE instruction processed...")
                print("Valve state is set to " + changestate + " (" + str(changetimer) + " minutes)")
        except:
            print("BLE instruction request failed")
            pass
        # Get and publish the state from the valve
        publishvalvestate()            


def getblecode(status, timer):  
    # transform and prepair the mqtt request (on or off and time) to a BLE code
    valuerequest = ''  # type: str
    valuestatus = '00'
    valuecalltimer = '0000'
    valuecalltimer = hex(int(timer)).split('x')[-1]  # decimal to hex conversion for the off time
    valuecalltimer = valuecalltimer.rjust(4, '0')  # if the value is shorter than 4 digits fill them up with zeros
    if status == 'on':
        valuestatus = '01'
    elif status == 'off':
        valuestatus = '00'
    valuerequest += valuebase
    valuerequest += valuestatus
    valuerequest += valuecalltimer
    if trace:
        print("BLE instruction: " + valuerequest)
    return bytearray.fromhex(valuerequest).decode()


def setblerequest(status, devicemac, bleHandle, bleValue):
    # Send the BLE request code via bluetooth to the valve
    global deviceblelock
    if trace:
        print("Connecting " + devicemac + "...")
    if deviceblelock == False:
        deviceblelock = True
        try:
            device = btle.Peripheral(str(devicemac))
            bleValueByte = bleValue.encode('utf-8')
            device.writeCharacteristic(0x0073, bleValueByte, withResponse=True)
        except btle.BTLEDisconnectError:
            print("Connection error when sending BLE message to valve (BTLEDisconnectError)")
            pass
        if trace:
            print("Wait " + str(waittime) + " seconds...")
        time.sleep(int(waittime))
        device.disconnect()
        deviceblelock = False
    else:
        time.sleep(5)
        setblerequest(status, devicemac, bleHandle, bleValue)


def publishvalvestate():
    # Publish valve state and timer value of valve to a mqtt state message
    for i, devicemac in enumerate(devicemaclist):
        try:
            intsolenoid = getvalvestate(devicemac)
            intsolenoidstate = intsolenoid[0]
            intsolenoidtimer = intsolenoid[1]
            if intsolenoidstate == 0:
                status = 'off'
                schedule.clear('publish-valve-state')
                schedule.every(intvalveintervaloff).seconds.do(publishvalvestate).tag('publish-valve-state')
            elif intsolenoidstate == 1:
                status = 'on'
                schedule.clear('publish-valve-state')
                schedule.every(intvalveintervalon).seconds.do(publishvalvestate).tag('publish-valve-state')
            client.publish(mqttbasepath_aqualin + str(devicemac) + '/valvestate', status, qos=0, retain=True)
            client.publish(mqttbasepath_aqualin + str(devicemac) + '/valvetimer', str(intsolenoidtimer), qos=0, retain=True) 
            if trace:
                print("Valve state is " + status + " for device " + str(devicemac) + " (" + str(intsolenoidtimer) + " minutes)")
        except TypeError:
            print("Valve is not available")
        time.sleep(int(waittime))


def getvalvestate(devicemac):
    # Connect to valve and get state and timer value of a valve
    global deviceblelock
    if trace:
        print("Connecting " + devicemac + "...")
    if deviceblelock == False:
        deviceblelock = True
        try:
            device = btle.Peripheral(str(devicemac))
            intvalvestate = int(str(binascii.b2a_hex(device.readCharacteristic(0x0073)).decode('utf-8'))[4:6],16)
            intvalvetimer = int(str(binascii.b2a_hex(device.readCharacteristic(0x0073)).decode('utf-8'))[6:10],16)
            time.sleep(int(waittime))
            device.disconnect()
            deviceblelock = False
            return intvalvestate, intvalvetimer
        except btle.BTLEDisconnectError:
            print("Connection error during connection with valve (BTLEDisconnectError)")
            deviceblelock = False
    else:
        time.sleep(5)


def runmiflora():
    # Get MiFlora sensor data and send with MQTT
    global deviceblelock
    while True:
        if not deviceblelock:
            deviceblelock = True
            for plant in miplant_lib.MiPlant.discover(interface_index=0, timeout=5):
                if trace:
                    print('--------------------------')
                    print(('Address: %s' % plant.address))
                    print(('Battery level: %i%%' % plant.battery))
                    print(('Firmware: %s' % plant.firmware))
                    print(('Temperature: %.01f °C' % plant.temperature))
                    print(('Light: %.0f lx' % plant.light))
                    print(('Moisture: %.0f%%' % plant.moisture))
                    print(('Conductivity: %.0f µS/cm' % plant.conductivity))
                # publish Battery level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Battery', plant.battery, qos=0, retain=True)
                # publish Firmware level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Firmware', plant.firmware, qos=0, retain=True)
                # publish Temperature level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Temperature', plant.temperature, qos=0, retain=True)
                # publish Light level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Light', plant.light, qos=0, retain=True)
                # publish Moisture level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Moisture', plant.moisture, qos=0, retain=True)
                # publish Conductivity level Miflora
                client.publish(mqttbasepath_miflora + str(plant.address) + '/Conductivity', plant.conductivity, qos=0, retain=True)
            deviceblelock = False
            break
        time.sleep(int(waittime))


def runworkerblestatus():
    while True:
        schedule.run_pending()
        time.sleep(int(waittime))


if __name__ == '__main__':
   
    # First thread creating Queue for state calls
    t1 = Thread(target=runworkerbledevice)  # Start worker thread 1
    t1.setDaemon(True)
    t1.start()

    # Setting itteration check miflora sensors status every x minutes (where x = intmiflora)
    schedule.every(intmiflorainterval).seconds.do(runmiflora)  # for checking thread fullfillment

    # Setting itteration check valve status every x min (where x = intvalveintervaloff) and reporting on MQTT
    schedule.every(intvalveintervaloff).seconds.do(publishvalvestate).tag('publish-valve-state')  # for checking thread fullfillment

    # Second thread status checks all devices
    t2 = Thread(target=runworkerblestatus)  # Start worker thread 2
    t2.setDaemon(True)
    t2.start()

    # MQTT Startup
    client = mqtt.Client()
    client.username_pw_set(strmqttusername, strmqttpassword)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(strmqtthost, intmqttport, 60)
    client.loop_forever()
