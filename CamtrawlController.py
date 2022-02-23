#!/usr/bin/env python3
'''

'''

import datetime
import logging
from PyQt5 import QtCore
from SerialMonitor import SerialDevice


class CamtrawlController(QtCore.QObject):
    '''CamtrawlController provides a simple interface for interacting with
    the Camtrawl system controller. The Camtrawl system controller provides
    power management, sensor integration, and camera and strobe triggering
    for the Camtrawl platform.
    '''

    #  define CamtrawlController signals
    sensorData = QtCore.pyqtSignal(str, str, datetime.datetime, str)
    systemState = QtCore.pyqtSignal(int)
    txSerialData = QtCore.pyqtSignal(str, str)
    error = QtCore.pyqtSignal(str,str)
    stopDevice = QtCore.pyqtSignal(list)
    controllerStopped = QtCore.pyqtSignal()


    #  define the controller states
    SLEEP = 0
    FORCED_ON = 1
    AT_DEPTH = 2
    PRESSURE_SW_CLOSED = 3
    FORCE_ON_REMOVED =4
    SHALLOW = 5
    PRESSURE_SW_OPENED = 6
    LOW_BATT = 7
    PC_ERROR = 8


    def __init__(self, serial_port='COM3', baud=115200, parent=None):

        super(CamtrawlController, self).__init__(parent)

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        
        self.isRunning = False

        #  set the serial port poroperties
        self.deviceParams = {'deviceName':'CTControl',
                             'port':str(serial_port),
                             'baud':int(baud),
                             'parseType':'None',
                             'parseExp':'',
                             'parseIndex':0,
                             'pollRate':500,
                             'txRate':250,
                             'initialState':(True,True),
                             'cmdPrompt':'',
                             'byteSize':8,
                             'parity':'N',
                             'stopBits':1,
                             'flowControl':'NONE',
                             'thread':None}


    def startController(self):
        """startController opens the serial connection to the controller
        """

        #  create a SerialDevice object
        self.serialDevice = SerialDevice.SerialDevice(self.deviceParams)

        #  connect the SerialDevice's signals
        self.serialDevice.SerialDataReceived.connect(self.sensorDataReceived)
        self.serialDevice.SerialError.connect(self.serialError)

        #  and connect our stop signal
        self.stopDevice.connect(self.serialDevice.stopPolling)
        self.txSerialData.connect(self.serialDevice.write)

        #  create a thread to run the serial device
        self.deviceParams['thread'] = QtCore.QThread(self)

        #  move the device to it
        self.serialDevice.moveToThread(self.deviceParams['thread'])

        #  connect thread specific signals and slots - this facilitates starting,
        #  stopping, and deletion of the thread.
        self.deviceParams['thread'].started.connect(self.serialDevice.startPolling)
        self.deviceParams['thread'].started.connect(self.controllerStarted)
        self.serialDevice.SerialPortClosed.connect(self.deviceParams['thread'].quit)
        self.deviceParams['thread'].finished.connect(self.threadFinished)
        self.deviceParams['thread'].finished.connect(self.deviceParams['thread'].deleteLater)

        self.logger.debug("Starting CamtrawlController. Port: " + self.deviceParams['port'] +
                "   Baud: " + str(self.deviceParams['baud']))

        #  queue up a controller state request - this will not be sent until
        #  the port is opened and starts polling.
        self.getSystemState()

        #  and finally, start the thread - this will also start polling
        self.deviceParams['thread'].start()


    def controllerStarted(self):
        """
        controllerStarted is called after the serial thread starts
        """
        self.isRunning = True


    def stopController(self):
        """
          stopController emits the ``stopDevice`` signal which informs the
          SerialDevice thread to stop polling, flush and close the serial port, and
          terminate the thread.

        """
        self.logger.debug("Stopping CamtrawlController...")
        self.stopDevice.emit([self.deviceParams['deviceName']])


    def sendReadySignal(self):
        '''sendReadySignal sends the "System Ready" signal to the controller
        to indicate that the PC has booted and the acquisition software has
        successfully started.
        '''
        msg = "setPCState,1\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def sendShutdownSignal(self):
        '''sendShutdownSignal sends the controller the "shutdown" signal indicating
        that the PC is shutting down. The PC typically only signals the controller
        to shut down when there is an unrecoverable acquisition error.
        '''
        msg = "setPCState,255\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def sendShutdownAckSignal(self):
        '''sendShutdownAckSignal is sent after the shutdown signal is received from
        the controller and the PC is starting the shutdown process.
        '''
        msg = "setPCState,0\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getSystemState(self):
        '''getSystemState requests the controllers current state.
        '''

        msg = "getState\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setSystemState(self, state):
        '''setSystemState sets the controller's current state.
        '''
        msg = "setState," + str(state) + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getStrobeMode(self):
        '''getStrobeMode requests the current strobe mode from the controller.
        '''
        msg = "getStrobeMode\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setStrobeMode(self, mode):
        '''setStrobeMode is used to set the strobe mode.
        '''
        msg = "setStrobeMode," + str(mode) + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setRTCParameters(self, installed, startDelay):

        if installed > 1:
            installed = 1;
        elif installed < 0:
            installed = 0

        msg = "setRTCPar," + str(installed) + "," + str(startDelay) + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getRTCParameters(self):

        msg = "getRTCPar\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getRTC(self):

        msg = "getRTC\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setRTC(self, time=None):
        '''setRTC sets the controller's RTC to the time specified as a datetime
        object. If no object is passed, the current time is used.

        '''

        if time is None:
            time = datetime.datetime.now()

        msg = "setRTC," + time.strftime("%Y,%m,%d,%H,%M,%S") + "\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def setP2DParameters(self, enabled, slope, intercept, turnOnDepth, turnOffDepth):

        if enabled > 1:
            enabled = 1
        elif enabled < 0:
            enabled = 0

        turnOnDepth = round(turnOnDepth)
        turnOnDepth = round(turnOffDepth)

        msg = ("setP2DParms," + str(enabled) +
                "," + str(slope) + "," + str(intercept) + "," + str(turnOnDepth) + "," +
                str(turnOffDepth) + "\n")
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getP2DParameters(self):

        msg = "getP2DParms\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getStartupVoltage(self):

        msg = "getStartupVoltage\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def getShutdownVoltage(self):
        '''
        '''

        msg = "getShutdownVoltage\n"
        self.txSerialData.emit(self.deviceParams['deviceName'], msg)
        self.logger.debug("CamtrawlController sent: " + msg)


    def trigger(self, strobePreFire, strobe1Exp, strobe2Exp, chanOneTrig, chanTwoTrig):
        '''trigger sends the trigger command to the controller

        Args:
            strobePreFire (uint):
                The time, in microseconds, the strobes will be triggered before the cameras.
                This provides time for LED "strobes" to reach full brightness.
            strobe1Exp (uint):
                The time, in microseconds, of the strobe channel 1 trigger. MACE/AFSC LED
                strobes are on when the trigger signal is high and this value sets the strobe
                exposure time for strobe channel 1.
            strobe2Exp (uint):
                The time, in microseconds, of the strobe channel 2 trigger. MACE/AFSC LED
                strobes are on when the trigger signal is high and this value sets the strobe
                exposure time for strobe channel 2.
            chanOneTrig (bool):
                Set this argument to True to trigger camera 1.
            chanTwoTrig (bool):
                Set this argument to True to trigger camera 2

        Returns:
            None

        '''

        chanOneTrig = bool(chanOneTrig)
        if chanOneTrig:
            chanOneTrig = 1
        else:
            chanOneTrig = 0

        chanTwoTrig = bool(chanTwoTrig)
        if chanTwoTrig:
            chanTwoTrig = 1
        else:
            chanTwoTrig = 0

        strobePreFire = int(round(strobePreFire))
        strobe1Exp = int(round(strobe1Exp))
        strobe2Exp = int(round(strobe2Exp))

        msg = ("trigger," + str(strobePreFire) + "," + str(strobe1Exp) +
                "," + str(strobe2Exp) + "," + str(chanOneTrig) +
                "," + str(chanTwoTrig) + "\n")

        self.txSerialData.emit(self.deviceParams['deviceName'], msg)

        self.logger.debug("CamtrawlController sent: " + msg)


    @QtCore.pyqtSlot(str, str, object)
    def sensorDataReceived(self, sensorID, data, err):
        '''The sensorDataReceived slot is called when serial data is available

        Args:
            sensorID (str):
                A string representing the sensor ID.
            data (str):
                A string containing the serial data message.
            err (object):
                DESCRIPTION

        Returns:
            None
        '''

        #  here we process the various datagrams received from the controller.
        rxTime = datetime.datetime.now()
        dataBits = data.split(',')
        header = dataBits[0]

        if header == "getState":
            # Convert the state to an int and emit the systemState signal
            state = int(dataBits[1])
            self.systemState.emit(state)
        else:
            #  re-emit everything else
            self.sensorData.emit(sensorID, header, rxTime, data)


    @QtCore.pyqtSlot(str, object)
    def serialError(self, sensorID, errorObj):

        self.logger.error("CamtrawlControl serial error [" + self.deviceParams['port']
                + "," + str(self.deviceParams['baud']) + ']:' + str(errorObj.errText))
        self.logger.error("    " + str(errorObj.parent))

        #  re-emit the error signal
        self.error.emit('CamtrawlControl', str(errorObj.errText))


    @QtCore.pyqtSlot()
    def threadFinished(self):
        """
          threadFinished is called when the SerialDevice thread instance finishes
          running and emits the "finished" signal.
        """

        #  discard our reference to the thread
        self.deviceParams['thread'] = None
        self.isRunning = False
        self.controllerStopped.emit()

        self.logger.debug("CamtrawlController stopped.")

