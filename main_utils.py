#-------------------------------------------------------------------------------
# Name:        main_Utils
# Purpose:     Class functions libary
#
# Author:      roy.oltmans
#
# Created:     23-10-2014
# Copyright:   (c) 2020 Ernst Klamer, roy.oltmans
# Licence:     MIT license
#-------------------------------------------------------------------------------

import os, configparser

class tools(object):
    #Fetch Configuration
    def fetchConfig(self):
        Config = configparser.ConfigParser()
        ConfigFilePath = os.path.dirname(os.path.abspath(__file__)).replace(' ','\ ') + "/config.ini"
        Config.read(ConfigFilePath)
        return Config
